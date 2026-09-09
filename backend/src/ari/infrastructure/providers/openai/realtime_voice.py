from __future__ import annotations

import json
import time
from collections import deque
from collections.abc import AsyncIterator
from contextlib import suppress
from hashlib import sha256
from typing import Any

import httpx
from websockets.asyncio.client import ClientConnection, connect

from ari.application.contracts import ExecutionContext
from ari.application.ports.realtime_voice import (
    RealtimeCall,
    RealtimeProviderEvent,
    RealtimeResponseKind,
    RealtimeSimulationSpec,
)
from ari.domain.errors import ProviderError
from ari.domain.models import (
    CostStatus,
    ExecutionRecord,
    ExecutionStatus,
    InteractionMode,
    VoiceProfile,
    new_id,
)
from ari.infrastructure.providers.openai.pricing import (
    PRICING_VERSION,
    CostResult,
    calculate_realtime_cost,
)


class RealtimeAPIError(Exception):
    def __init__(self, status_code: int, body: str) -> None:
        super().__init__(f"OpenAI Realtime HTTP {status_code}: {body[:800]}")
        self.status_code = status_code


class OpenAIRealtimeCall:
    provider = "openai"

    def __init__(
        self,
        *,
        socket: ClientConnection,
        call_id: str,
        model: str,
        answer_sdp: str,
        execution: ExecutionRecord,
        context: ExecutionContext,
        prompt_version: str,
        prompt_hash: str,
    ) -> None:
        self._socket = socket
        self.call_id = call_id
        self.model = model
        self.answer_sdp = answer_sdp
        self.execution = execution
        self._context = context
        self._prompt_version = prompt_version
        self._prompt_hash = prompt_hash
        self._active_response_id: str | None = None
        self._response_by_item: dict[str, str] = {}
        self._speech_started: set[str] = set()
        self._pending_response_kinds: deque[RealtimeResponseKind] = deque()
        self._response_kinds: dict[str, RealtimeResponseKind] = {}

    async def events(self) -> AsyncIterator[RealtimeProviderEvent]:
        async for raw_message in self._socket:
            payload: dict[str, Any] = json.loads(raw_message)
            mapped = self._map_event(payload)
            if mapped is not None:
                yield mapped

    async def close(self) -> None:
        await self._socket.close()

    async def update_simulation(self, simulation: RealtimeSimulationSpec) -> None:
        await self._socket.send(
            json.dumps(
                {
                    "type": "session.update",
                    "session": {
                        "type": "realtime",
                        "instructions": simulation.instructions,
                    },
                }
            )
        )

    async def begin_user_turn(self) -> None:
        await self._socket.send(json.dumps({"type": "input_audio_buffer.clear"}))

    async def commit_user_turn(self) -> None:
        await self._socket.send(json.dumps({"type": "input_audio_buffer.commit"}))

    async def interrupt_response(self) -> None:
        try:
            if self._active_response_id is not None:
                await self._socket.send(json.dumps({"type": "response.cancel"}))
            await self._socket.send(json.dumps({"type": "output_audio_buffer.clear"}))
        except Exception as exc:
            raise ProviderError(
                "OpenAI Realtime interruption failed",
                execution=self._command_error_execution("realtime_voice.interrupt", exc),
            ) from exc

    async def create_response(
        self,
        *,
        kind: RealtimeResponseKind = RealtimeResponseKind.PATIENT_ANSWER,
        exact_text: str | None = None,
    ) -> None:
        response: dict[str, object] = {"metadata": {"ari_response_kind": kind.value}}
        if exact_text is not None:
            response.update(
                {
                    "input": [],
                    "instructions": (
                        "Speak exactly the following text, word for word, without adding or "
                        f"removing anything: {json.dumps(exact_text, ensure_ascii=False)}"
                    ),
                }
            )
        self._pending_response_kinds.append(kind)
        try:
            await self._socket.send(json.dumps({"type": "response.create", "response": response}))
        except Exception as exc:
            self._pending_response_kinds.pop()
            raise ProviderError(
                "OpenAI Realtime response creation failed",
                execution=self._command_error_execution("realtime_voice.response_create", exc),
            ) from exc

    def _response_kind(self, response_id: str | None) -> RealtimeResponseKind:
        if response_id is None:
            return RealtimeResponseKind.PATIENT_ANSWER
        return self._response_kinds.get(response_id, RealtimeResponseKind.PATIENT_ANSWER)

    def _map_event(self, payload: dict[str, Any]) -> RealtimeProviderEvent | None:
        event_type = str(payload.get("type", ""))
        input_item_id = _optional_str(payload.get("item_id"))
        response_id = _optional_str(payload.get("response_id"))
        if event_type == "input_audio_buffer.speech_started":
            return RealtimeProviderEvent(
                "user_speech_started",
                input_item_id=input_item_id,
                response_id=self._active_response_id,
                raw=payload,
            )
        if event_type == "input_audio_buffer.speech_stopped":
            return RealtimeProviderEvent(
                "user_speech_stopped", input_item_id=input_item_id, raw=payload
            )
        if event_type == "conversation.item.input_audio_transcription.delta":
            return RealtimeProviderEvent(
                "user_transcript_delta",
                text=str(payload.get("delta", "")),
                input_item_id=input_item_id,
                raw=payload,
            )
        if event_type == "conversation.item.input_audio_transcription.completed":
            return RealtimeProviderEvent(
                "user_transcript_final",
                text=str(payload.get("transcript", "")),
                input_item_id=input_item_id,
                raw=payload,
            )
        if event_type == "response.created":
            response = payload.get("response")
            details = response if isinstance(response, dict) else {}
            response_id = _optional_str(details.get("id"))
            self._active_response_id = response_id
            metadata = details.get("metadata")
            metadata_dict = metadata if isinstance(metadata, dict) else {}
            raw_kind = metadata_dict.get("ari_response_kind")
            try:
                kind = RealtimeResponseKind(str(raw_kind))
            except ValueError:
                kind = (
                    self._pending_response_kinds.popleft()
                    if self._pending_response_kinds
                    else RealtimeResponseKind.PATIENT_ANSWER
                )
            else:
                if self._pending_response_kinds:
                    self._pending_response_kinds.popleft()
            if response_id is not None:
                self._response_kinds[response_id] = kind
            return RealtimeProviderEvent(
                "response_created",
                response_id=response_id,
                response_kind=kind,
                raw=payload,
            )
        if event_type in {"response.output_audio.delta", "response.audio.delta"}:
            audio_response_id = response_id or self._active_response_id
            if audio_response_id is None or audio_response_id in self._speech_started:
                return None
            self._speech_started.add(audio_response_id)
            return RealtimeProviderEvent(
                "patient_speech_started",
                response_id=audio_response_id,
                response_kind=self._response_kind(audio_response_id),
                raw=payload,
            )
        if event_type in {
            "response.output_audio_transcript.delta",
            "response.audio_transcript.delta",
        }:
            if input_item_id and response_id:
                self._response_by_item[input_item_id] = response_id
            return RealtimeProviderEvent(
                "patient_transcript_delta",
                text=str(payload.get("delta", "")),
                input_item_id=input_item_id,
                response_id=response_id or self._active_response_id,
                response_kind=self._response_kind(response_id or self._active_response_id),
                raw=payload,
            )
        if event_type in {
            "response.output_audio_transcript.done",
            "response.audio_transcript.done",
        }:
            return RealtimeProviderEvent(
                "patient_transcript_final",
                text=str(payload.get("transcript", "")),
                input_item_id=input_item_id,
                response_id=response_id or self._active_response_id,
                response_kind=self._response_kind(response_id or self._active_response_id),
                raw=payload,
            )
        if event_type == "conversation.item.truncated":
            interrupted_response = (
                self._response_by_item.get(input_item_id or "") or self._active_response_id
            )
            return RealtimeProviderEvent(
                "patient_interrupted",
                input_item_id=input_item_id,
                response_id=interrupted_response,
                response_kind=self._response_kind(interrupted_response),
                audio_end_ms=(
                    int(payload["audio_end_ms"])
                    if isinstance(payload.get("audio_end_ms"), int)
                    else None
                ),
                raw=payload,
            )
        if event_type == "response.done":
            response = payload.get("response")
            details = response if isinstance(response, dict) else {}
            completed_id = _optional_str(details.get("id")) or response_id
            usage = details.get("usage")
            usage_dict = usage if isinstance(usage, dict) else {}
            response_status = str(details.get("status", "completed"))
            status_details = details.get("status_details")
            response_kind = self._response_kind(completed_id)
            self._active_response_id = None
            return RealtimeProviderEvent(
                "response_completed",
                response_id=completed_id,
                response_status=response_status,
                usage=usage_dict,
                execution=(
                    _response_execution(
                        context=self._context,
                        model=self.model,
                        prompt_version=self._prompt_version,
                        prompt_hash=self._prompt_hash,
                        response_id=completed_id,
                        usage=usage_dict,
                        response_status=response_status,
                        status_details=(status_details if isinstance(status_details, dict) else {}),
                        response_kind=response_kind,
                    )
                    if completed_id
                    else None
                ),
                response_kind=response_kind,
                raw=payload,
            )
        if event_type == "error":
            error_kind = (
                self._response_kind(self._active_response_id)
                if self._active_response_id is not None
                else (
                    self._pending_response_kinds.popleft()
                    if self._pending_response_kinds
                    else RealtimeResponseKind.PATIENT_ANSWER
                )
            )
            return RealtimeProviderEvent(
                "error",
                execution=self._provider_error_execution(payload),
                response_kind=error_kind,
                raw=payload,
            )
        return None

    def _provider_error_execution(self, payload: dict[str, Any]) -> ExecutionRecord:
        error = payload.get("error")
        details = error if isinstance(error, dict) else {}
        return ExecutionRecord(
            id=new_id(),
            session_id=self._context.session_id,
            operation="realtime_voice.event",
            provider="openai",
            model=self.model,
            status=ExecutionStatus.FAILED,
            prompt_version=self._prompt_version,
            prompt_hash=self._prompt_hash,
            case_version=self._context.case_version,
            case_hash=self._context.case_hash,
            latency_ms=0,
            usage={},
            pricing_version=PRICING_VERSION,
            provider_request_id=_optional_str(payload.get("event_id")),
            error_code=str(details.get("code", "realtime_error")),
            error_message=str(details.get("message", "Realtime provider error"))[:1000],
            retryable=str(details.get("code", "")) in {"server_error", "rate_limit_exceeded"},
        )

    def _command_error_execution(self, operation: str, error: Exception) -> ExecutionRecord:
        return ExecutionRecord(
            id=new_id(),
            session_id=self._context.session_id,
            operation=operation,
            provider="openai",
            model=self.model,
            status=ExecutionStatus.FAILED,
            prompt_version=self._prompt_version,
            prompt_hash=self._prompt_hash,
            case_version=self._context.case_version,
            case_hash=self._context.case_hash,
            latency_ms=0,
            usage={},
            pricing_version=PRICING_VERSION,
            error_code=type(error).__name__,
            error_message=str(error)[:1000],
            retryable=True,
        )


class OpenAIRealtimeVoiceEngine:
    def __init__(
        self,
        api_key: str,
        *,
        calls_url: str = "https://api.openai.com/v1/realtime/calls",
        sideband_url: str = "wss://api.openai.com/v1/realtime",
        timeout_seconds: float = 15.0,
        quality_model: str = "gpt-realtime-2.1",
        economy_model: str = "gpt-realtime-2.1-mini",
    ) -> None:
        self._api_key = api_key
        self._calls_url = calls_url
        self._sideband_url = sideband_url
        self._timeout_seconds = timeout_seconds
        self._profile_models = {
            VoiceProfile.QUALITY: quality_model,
            VoiceProfile.ECONOMY: economy_model,
        }

    async def start_call(
        self,
        offer_sdp: str,
        *,
        context: ExecutionContext,
        simulation: RealtimeSimulationSpec,
        profile: VoiceProfile,
        interaction_mode: InteractionMode,
    ) -> RealtimeCall:
        started = time.perf_counter()
        model = self._profile_models[profile]
        session = {
            "type": "realtime",
            "model": model,
            "output_modalities": ["audio"],
            "instructions": simulation.instructions,
            "audio": {
                "input": {
                    "transcription": {
                        "model": "gpt-4o-mini-transcribe",
                        "language": _primary_language(simulation.language),
                        "prompt": simulation.transcription_context,
                    },
                    "turn_detection": (
                        None
                        if interaction_mode is InteractionMode.GUIDED
                        else {
                            "type": "semantic_vad",
                            "eagerness": "medium",
                            "create_response": False,
                            "interrupt_response": True,
                        }
                    ),
                    "noise_reduction": {"type": "near_field"},
                },
                "output": {"voice": "marin"},
            },
            "reasoning": {"effort": "low"},
            "tools": [],
        }
        socket: ClientConnection | None = None
        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.post(
                    self._calls_url,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "OpenAI-Safety-Identifier": sha256(
                            (context.learner_id or context.session_id).encode()
                        ).hexdigest(),
                    },
                    files={
                        "sdp": (None, offer_sdp),
                        "session": (None, json.dumps(session)),
                    },
                )
            if response.is_error:
                raise RealtimeAPIError(response.status_code, response.text)
            location = response.headers.get("location", "")
            call_id = location.rstrip("/").rsplit("/", maxsplit=1)[-1]
            if not call_id.startswith("rtc_"):
                raise ValueError("OpenAI Realtime response did not contain a call id")
            socket = await connect(
                f"{self._sideband_url}?call_id={call_id}",
                additional_headers={"Authorization": f"Bearer {self._api_key}"},
                open_timeout=self._timeout_seconds,
            )
            execution = _execution(
                context=context,
                model=model,
                started=started,
                status=ExecutionStatus.SUCCEEDED,
                prompt_version=simulation.prompt_version,
                prompt_hash=simulation.prompt_hash,
                call_id=call_id,
            )
            return OpenAIRealtimeCall(
                socket=socket,
                call_id=call_id,
                model=model,
                answer_sdp=response.text,
                execution=execution,
                context=context,
                prompt_version=simulation.prompt_version,
                prompt_hash=simulation.prompt_hash,
            )
        except Exception as exc:
            if socket is not None:
                with suppress(Exception):
                    await socket.close()
            execution = _execution(
                context=context,
                model=model,
                started=started,
                status=ExecutionStatus.FAILED,
                prompt_version=simulation.prompt_version,
                prompt_hash=simulation.prompt_hash,
                error=exc,
            )
            raise ProviderError("OpenAI Realtime connection failed", execution=execution) from exc


def _response_execution(
    *,
    context: ExecutionContext,
    model: str,
    prompt_version: str,
    prompt_hash: str,
    response_id: str,
    usage: dict[str, object],
    response_status: str,
    status_details: dict[str, object],
    response_kind: RealtimeResponseKind,
) -> ExecutionRecord:
    succeeded = response_status == "completed"
    cost = calculate_realtime_cost(model, usage)
    return ExecutionRecord(
        id=new_id(),
        session_id=context.session_id,
        operation=(
            "realtime_voice.opening"
            if response_kind is RealtimeResponseKind.OPENING
            else "realtime_voice.response"
        ),
        provider="openai",
        model=model,
        status=(ExecutionStatus.SUCCEEDED if succeeded else ExecutionStatus.FAILED),
        prompt_version=prompt_version,
        prompt_hash=prompt_hash,
        case_version=context.case_version,
        case_hash=context.case_hash,
        latency_ms=0,
        usage={**usage, "response_kind": response_kind.value},
        estimated_cost_usd=cost.amount_usd,
        pricing_version=cost.pricing_version,
        cost_status=cost.status,
        cost_amount_usd=cost.amount_usd,
        cost_units=dict(cost.units),
        cost_assumptions=cost.assumptions,
        cost_unknown_reason=cost.unknown_reason,
        provider_request_id=response_id,
        error_code=None if succeeded else f"realtime_response_{response_status}",
        error_message=None if succeeded else json.dumps(status_details)[:1000],
        retryable=response_status in {"failed", "incomplete"},
    )


def _execution(
    *,
    context: ExecutionContext,
    model: str,
    started: float,
    status: ExecutionStatus,
    prompt_version: str,
    prompt_hash: str,
    call_id: str | None = None,
    error: Exception | None = None,
) -> ExecutionRecord:
    cost = (
        CostResult(
            component="realtime_connect",
            pricing_version=PRICING_VERSION,
            status=CostStatus.EXACT,
            amount_usd=0.0,
            assumptions=("connection setup has no token usage",),
            source_urls=(),
        )
        if status is ExecutionStatus.SUCCEEDED
        else None
    )
    return ExecutionRecord(
        id=new_id(),
        session_id=context.session_id,
        operation="realtime_voice.connect",
        provider="openai",
        model=model,
        status=status,
        prompt_version=prompt_version,
        prompt_hash=prompt_hash,
        case_version=context.case_version,
        case_hash=context.case_hash,
        latency_ms=int((time.perf_counter() - started) * 1000),
        usage={},
        estimated_cost_usd=cost.amount_usd if cost is not None else None,
        pricing_version=cost.pricing_version if cost is not None else PRICING_VERSION,
        cost_status=cost.status if cost is not None else CostStatus.UNKNOWN,
        cost_amount_usd=cost.amount_usd if cost is not None else None,
        cost_units=dict(cost.units) if cost is not None else {},
        cost_assumptions=cost.assumptions if cost is not None else (),
        cost_unknown_reason=(
            None if status is ExecutionStatus.SUCCEEDED else "Connection failed without usage"
        ),
        provider_request_id=call_id,
        error_code=type(error).__name__ if error else None,
        error_message=str(error)[:1000] if error else None,
        retryable=(
            error is not None
            and (
                not isinstance(error, RealtimeAPIError)
                or error.status_code in {408, 409, 429}
                or error.status_code >= 500
            )
        ),
    )


def _optional_str(value: object) -> str | None:
    return str(value) if value else None


def _primary_language(locale: str) -> str:
    return locale.strip().replace("_", "-").split("-", maxsplit=1)[0].lower()
