"""Placement test: adaptive MCQ staircase, short listening, two spoken tasks.

Everything except the spoken rating is deterministic and versioned
(`placement-staircase-v1`). The result is an estimate per skill and an overall band,
copied to the learner profile as `estimated_level`; it is never a certificate.
"""

from __future__ import annotations

import json
import random
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from hashlib import sha256
from typing import Any

from ari.application.contracts import ExecutionContext, LLMRequest, TranscriptionConfig
from ari.application.ports.llm import LLMProvider
from ari.application.ports.repository import SessionRepository
from ari.application.ports.stt import UtteranceTranscriber
from ari.application.ports.tts import StreamingTTSProvider
from ari.application.prompting import VersionedPrompt
from ari.application.schemas import SpeakingRatingSchema
from ari.domain.errors import InvalidStateError, NotFoundError
from ari.domain.models import CEFRLevel, ExecutionRecord, new_id, utc_now
from ari.domain.placement import (
    LEVELS,
    LISTENING_ITEMS,
    PLACEMENT_METHOD,
    STAIRCASE_MAX_ITEMS,
    STAIRCASE_MAX_REVERSALS,
    ListeningItem,
    McqItem,
    PlacementSetVersion,
    SpeakingItem,
    band,
    estimate_level,
    level_index,
    listening_level,
    next_level,
    shift_level,
)
from ari.infrastructure.cases.placement_store import PlacementStore
from ari.infrastructure.persistence.placement import PlacementAttempt, SqlPlacementRepository

DEFAULT_START_LEVEL = "A2"


def _execution_summary(execution: ExecutionRecord) -> dict[str, Any]:
    return {
        "operation": execution.operation,
        "provider": execution.provider,
        "model": execution.model,
        "status": execution.status.value,
        "prompt_version": execution.prompt_version,
        "prompt_hash": execution.prompt_hash,
        "latency_ms": execution.latency_ms,
        "usage": execution.usage,
    }


class PlacementService:
    def __init__(
        self,
        store: PlacementStore,
        attempts: SqlPlacementRepository,
        learners: SessionRepository,
        transcriber: UtteranceTranscriber,
        tts: StreamingTTSProvider,
        llm: LLMProvider,
        speaking_prompt: VersionedPrompt,
        *,
        feedback_language: str,
        sample_rate: int,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.store = store
        self.attempts = attempts
        self._learners = learners
        self._transcriber = transcriber
        self._tts = tts
        self._llm = llm
        self._prompt = speaking_prompt
        self._feedback_language = feedback_language
        self._sample_rate = sample_rate
        self._clock = clock
        self._audio_cache: dict[tuple[str, str], bytes] = {}

    # ----- read ----------------------------------------------------------------------

    def overview(self, learner_id: str) -> dict[str, Any]:
        placement_set = self.store.published()
        learner = self._learners.get_learner(learner_id)
        attempts = self.attempts.list(learner_id)
        latest = attempts[0] if attempts else None
        return {
            "available": placement_set is not None,
            "method": PLACEMENT_METHOD,
            "set": self._set_summary(placement_set) if placement_set else None,
            "latest": self.public_attempt(latest) if latest else None,
            "estimated_level": (
                learner.details.estimated_level.value if learner.details.estimated_level else None
            ),
            "estimated_at": (
                learner.details.estimated_at.isoformat() if learner.details.estimated_at else None
            ),
            "declared_level": (
                learner.details.declared_level.value if learner.details.declared_level else None
            ),
            "limitations": (
                "Niveau estimé par un test court : QCM adaptatif, écoute, deux productions "
                "orales notées automatiquement. Ce n'est ni un certificat ni le niveau requis "
                "pour l'inscription à la FSP."
            ),
        }

    @staticmethod
    def _set_summary(placement_set: PlacementSetVersion) -> dict[str, Any]:
        return {
            "id": placement_set.id,
            "version": placement_set.version,
            "hash": placement_set.content_hash,
            "language": placement_set.language,
            "title": placement_set.title,
            "description_fr": placement_set.description_fr,
            "mcq_items": len(placement_set.mcq_items),
            "listening_items": min(LISTENING_ITEMS, len(placement_set.listening_items)),
            "speaking_items": len(placement_set.speaking_items),
            "max_mcq": STAIRCASE_MAX_ITEMS,
        }

    def _pinned_set(self, attempt: PlacementAttempt) -> PlacementSetVersion:
        placement_set, _status = self.store.get(attempt.set_id, attempt.set_version)
        if placement_set.content_hash != attempt.set_hash:
            raise InvalidStateError("Le test épinglé a changé sans nouvelle version")
        return placement_set

    def get(self, learner_id: str, attempt_id: str) -> dict[str, Any]:
        return self.public_attempt(self.attempts.get(attempt_id, learner_id))

    # ----- start ---------------------------------------------------------------------

    def start(self, learner_id: str, request_id: str) -> dict[str, Any]:
        existing = self.attempts.find_request(learner_id, request_id)
        if existing is not None:
            return self.public_attempt(existing)
        placement_set = self.store.published()
        if placement_set is None:
            raise InvalidStateError("Aucun test de niveau publié")
        learner = self._learners.get_learner(learner_id)
        available = placement_set.mcq_levels()
        declared = learner.details.declared_level
        start = shift_level(declared.value if declared else DEFAULT_START_LEVEL, 0, available)
        attempt_id = new_id()
        order = {
            level: self._shuffled(
                attempt_id, [i.id for i in placement_set.mcq_items if i.level == level]
            )
            for level in available
        }
        state: dict[str, Any] = {
            "method": PLACEMENT_METHOD,
            "level": start,
            "start_level": start,
            "streak_correct": 0,
            "streak_wrong": 0,
            "direction": 0,
            "reversals": 0,
            "presented": [],
            "levels": [],
            "order": order,
            "listening_ids": [],
            "listening_correct": 0,
            "speaking_ids": [item.id for item in placement_set.speaking_items],
            "speaking": [],
            "current_item": None,
        }
        state["current_item"] = self._pick_mcq(state, available)
        attempt = self.attempts.create(
            PlacementAttempt(
                id=attempt_id,
                learner_id=learner_id,
                request_id=request_id,
                set_id=placement_set.id,
                set_version=placement_set.version,
                set_hash=placement_set.content_hash,
                status="active",
                phase="mcq",
                state=state,
                created_at=self._clock(),
            )
        )
        return self.public_attempt(attempt)

    @staticmethod
    def _shuffled(seed: str, ids: list[str]) -> list[str]:
        ordered = list(ids)
        random.Random(seed).shuffle(ordered)
        return ordered

    @staticmethod
    def _pick_mcq(state: dict[str, Any], available: tuple[str, ...]) -> str | None:
        """Next unseen item at the current staircase level; None ends the MCQ phase.

        Borrowing items from another level would let the staircase drift away from the
        level it actually reached and bias the estimate, so exhaustion stops the phase.
        """
        del available
        presented = set(state["presented"])
        for item_id in state["order"].get(state["level"], []):
            if item_id not in presented:
                return item_id
        return None

    # ----- answers -------------------------------------------------------------------

    def answer(
        self, learner_id: str, attempt_id: str, *, event_id: str, item_id: str, option_index: int
    ) -> dict[str, Any]:
        attempt = self.attempts.get(attempt_id, learner_id)
        replay = next((a for a in attempt.answers if a["event_id"] == event_id), None)
        if replay is not None:
            return self.public_attempt(attempt)
        if attempt.status != "active" or attempt.phase not in {"mcq", "listening"}:
            raise InvalidStateError("Aucune question à choix en cours")
        if item_id != attempt.state.get("current_item"):
            raise InvalidStateError("Question inattendue ou déjà répondue")
        placement_set = self._pinned_set(attempt)
        state = json.loads(json.dumps(attempt.state))
        if attempt.phase == "mcq":
            item = self._mcq(placement_set, item_id)
            correct = option_index == item.answer_index
            payload = {"option_index": option_index, "correct": correct, "level": item.level}
            next_phase = self._advance_mcq(state, placement_set, item, correct)
        else:
            listening = self._listening(placement_set, item_id)
            correct = option_index == listening.answer_index
            payload = {"option_index": option_index, "correct": correct, "level": listening.level}
            next_phase = self._advance_listening(state, placement_set, listening, correct)
        updated = self.attempts.append_answer(
            attempt_id,
            learner_id,
            event_id=event_id,
            item_id=item_id,
            phase=attempt.phase,
            payload=payload,
            state=state,
            next_phase=next_phase,
        )
        if next_phase == "completed":
            return self.finish(learner_id, attempt_id)
        return self.public_attempt(updated)

    def _advance_mcq(
        self,
        state: dict[str, Any],
        placement_set: PlacementSetVersion,
        item: McqItem,
        correct: bool,
    ) -> str:
        available = placement_set.mcq_levels()
        state["presented"].append(item.id)
        # The staircase level at which the item was presented (equal to the item level).
        state["levels"].append(state["level"])
        level, streak_correct, streak_wrong, direction = next_level(
            state["level"], correct, state["streak_correct"], state["streak_wrong"], available
        )
        if direction and state["direction"] and direction != state["direction"]:
            state["reversals"] += 1
        if direction:
            state["direction"] = direction
        state.update(level=level, streak_correct=streak_correct, streak_wrong=streak_wrong)
        next_item = self._pick_mcq(state, available)
        finished = (
            len(state["presented"]) >= STAIRCASE_MAX_ITEMS
            or state["reversals"] >= STAIRCASE_MAX_REVERSALS
            or next_item is None
        )
        if not finished:
            state["current_item"] = next_item
            return "mcq"
        state["vocab_grammar_level"] = estimate_level(state["levels"], state["level"])
        return self._enter_listening(state, placement_set)

    def _enter_listening(self, state: dict[str, Any], placement_set: PlacementSetVersion) -> str:
        level = state["vocab_grammar_level"]
        by_distance = sorted(
            placement_set.listening_items,
            key=lambda i: (abs(level_index(i.level) - level_index(level)), i.level, i.id),
        )
        # Two at the estimated level when possible, then the nearest neighbours.
        chosen = [i.id for i in by_distance][:LISTENING_ITEMS]
        state["listening_ids"] = self._shuffled(state["start_level"] + level, chosen)
        state["listening_correct"] = 0
        if not state["listening_ids"]:
            return self._enter_speaking(state)
        state["current_item"] = state["listening_ids"][0]
        return "listening"

    def _advance_listening(
        self,
        state: dict[str, Any],
        placement_set: PlacementSetVersion,
        item: ListeningItem,
        correct: bool,
    ) -> str:
        state["presented"].append(item.id)
        state["listening_correct"] += int(correct)
        remaining = [i for i in state["listening_ids"] if i not in state["presented"]]
        if remaining:
            state["current_item"] = remaining[0]
            return "listening"
        available = tuple(sorted({i.level for i in placement_set.listening_items}, key=level_index))
        state["listening_level"] = listening_level(
            state["vocab_grammar_level"],
            state["listening_correct"],
            len(state["listening_ids"]),
            available,
        )
        return self._enter_speaking(state)

    @staticmethod
    def _enter_speaking(state: dict[str, Any]) -> str:
        remaining = [i for i in state["speaking_ids"] if i not in state["presented"]]
        if not remaining:
            return "completed"
        state["current_item"] = remaining[0]
        return "speaking"

    # ----- speaking ------------------------------------------------------------------

    async def speak(
        self,
        learner_id: str,
        attempt_id: str,
        *,
        event_id: str,
        item_id: str,
        pcm16: bytes | None = None,
        text: str | None = None,
    ) -> dict[str, Any]:
        attempt = self.attempts.get(attempt_id, learner_id)
        if any(a["event_id"] == event_id for a in attempt.answers):
            return self.public_attempt(attempt)
        if attempt.status != "active" or attempt.phase != "speaking":
            raise InvalidStateError("Aucune production orale en cours")
        if item_id != attempt.state.get("current_item"):
            raise InvalidStateError("Consigne inattendue ou déjà répondue")
        placement_set = self._pinned_set(attempt)
        item = self._speaking(placement_set, item_id)
        executions: list[dict[str, Any]] = []
        context = ExecutionContext(
            session_id=attempt.id,
            operation="placement_speaking",
            case_version=placement_set.version,
            case_hash=placement_set.content_hash,
            prompt_version=self._prompt.version,
            prompt_hash=self._prompt.content_hash,
            learner_id=learner_id,
        )
        if text is None:
            if not pcm16 or len(pcm16) < self._sample_rate * 2:  # under one second of audio
                raise InvalidStateError("Enregistrement trop court")
            transcription = await self._transcriber.transcribe(
                pcm16,
                sample_rate=self._sample_rate,
                context=replace(context, operation="placement_stt"),
                config=TranscriptionConfig(
                    expected_locales=(placement_set.language,),
                    context_prompt=item.prompt_target,
                    prompt_version=f"placement-stt:{placement_set.id}@{placement_set.version}",
                    prompt_hash=sha256(item.prompt_target.encode()).hexdigest(),
                ),
            )
            executions.append(_execution_summary(transcription.execution))
            text = transcription.text
        transcript = text.strip()
        if not transcript:
            raise InvalidStateError("Aucune parole reconnue")
        rating = await self._rate(placement_set, item, transcript, context)
        executions.append(_execution_summary(rating["execution"]))
        state = json.loads(json.dumps(attempt.state))
        state["presented"].append(item.id)
        state["speaking"].append(
            {
                "item_id": item.id,
                "level": rating["estimated_level"],
                "confidence": rating["confidence"],
            }
        )
        next_phase = self._enter_speaking(state)
        updated = self.attempts.append_answer(
            attempt_id,
            learner_id,
            event_id=event_id,
            item_id=item_id,
            phase="speaking",
            payload={
                "transcript": transcript,
                "estimated_level": rating["estimated_level"],
                "confidence": rating["confidence"],
                "observations": rating["observations"],
                "executions": executions,
            },
            state=state,
            next_phase=next_phase,
        )
        if next_phase == "completed":
            return self.finish(learner_id, attempt_id)
        return self.public_attempt(updated)

    async def _rate(
        self,
        placement_set: PlacementSetVersion,
        item: SpeakingItem,
        transcript: str,
        context: ExecutionContext,
    ) -> dict[str, Any]:
        request = LLMRequest(
            messages=(
                {"role": "system", "content": self._prompt.content},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "test_language": placement_set.language,
                            "feedback_language": self._feedback_language,
                            "task": item.prompt_target,
                            "task_level_hint": item.level_hint,
                            "target_seconds": item.target_seconds,
                            "transcript": transcript,
                        },
                        ensure_ascii=False,
                    ),
                },
            ),
            context=context,
        )
        result = await self._llm.generate_structured(request, SpeakingRatingSchema)
        return {
            "estimated_level": result.value.estimated_level,
            "confidence": result.value.confidence,
            "observations": list(result.value.observations),
            "execution": result.execution,
        }

    # ----- audio ---------------------------------------------------------------------

    async def listening_audio(self, learner_id: str, attempt_id: str, item_id: str) -> bytes:
        """PCM16 mono at the configured sample rate for a listening item the learner reached."""
        attempt = self.attempts.get(attempt_id, learner_id)
        reachable = attempt.state.get("listening_ids", [])
        if item_id not in reachable:
            raise NotFoundError("Item d'écoute indisponible")
        placement_set = self._pinned_set(attempt)
        item = self._listening(placement_set, item_id)
        key = (placement_set.content_hash, item_id)
        cached = self._audio_cache.get(key)
        if cached is not None:
            return cached
        context = ExecutionContext(
            session_id=attempt.id,
            operation="placement_tts",
            case_version=placement_set.version,
            case_hash=placement_set.content_hash,
            learner_id=learner_id,
        )
        chunks: list[bytes] = []
        async for event in self._tts.stream(item.script, context):
            if event.type == "chunk" and event.data:
                chunks.append(event.data)
        audio = b"".join(chunks)
        self._audio_cache[key] = audio
        return audio

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    # ----- finish --------------------------------------------------------------------

    def finish(self, learner_id: str, attempt_id: str) -> dict[str, Any]:
        attempt = self.attempts.get(attempt_id, learner_id)
        if attempt.status == "completed":
            return self.public_attempt(attempt)
        state = attempt.state
        vocab_grammar = state.get("vocab_grammar_level")
        if not vocab_grammar:
            raise InvalidStateError("Terminez d'abord les questions à choix")
        listening = state.get("listening_level") or vocab_grammar
        ratings = state.get("speaking", [])
        speaking = LEVELS[min(level_index(r["level"]) for r in ratings)] if ratings else None
        confidence = sum(r["confidence"] for r in ratings) / len(ratings) if ratings else 0.0
        overall, counted = band(vocab_grammar, listening, speaking, confidence)
        result = {
            "method": PLACEMENT_METHOD,
            "state": "estimated",
            "vocab_grammar_level": vocab_grammar,
            "listening_level": listening,
            "speaking_level": speaking,
            "speaking_confidence": round(confidence, 3),
            "speaking_counted": counted,
            "band": overall,
            "mcq_answered": sum(1 for a in attempt.answers if a["phase"] == "mcq"),
            "listening_answered": sum(1 for a in attempt.answers if a["phase"] == "listening"),
            "speaking_answered": len(ratings),
            "start_level": state.get("start_level"),
        }
        finished = self.attempts.finish(attempt_id, learner_id, result)
        learner = self._learners.get_learner(learner_id)
        self._learners.update_learner(
            replace(
                learner,
                details=replace(
                    learner.details,
                    estimated_level=CEFRLevel(overall),
                    estimated_at=self._clock(),
                    placement_attempt_id=attempt_id,
                ),
            )
        )
        return self.public_attempt(finished)

    # ----- projections ---------------------------------------------------------------

    def public_attempt(self, attempt: PlacementAttempt) -> dict[str, Any]:
        """Never serialize answer keys or scripts before they are due."""
        placement_set = self._pinned_set(attempt)
        state = attempt.state
        current: dict[str, Any] | None = None
        current_id = state.get("current_item")
        if attempt.status == "active" and current_id:
            if attempt.phase == "mcq":
                mcq = self._mcq(placement_set, current_id)
                current = {
                    "id": mcq.id,
                    "kind": "mcq",
                    "skill": mcq.skill,
                    "stem": mcq.stem,
                    "options": list(mcq.options),
                }
            elif attempt.phase == "listening":
                listening = self._listening(placement_set, current_id)
                current = {
                    "id": listening.id,
                    "kind": "listening",
                    "question": listening.question,
                    "options": list(listening.options),
                }
            elif attempt.phase == "speaking":
                speaking = self._speaking(placement_set, current_id)
                current = {
                    "id": speaking.id,
                    "kind": "speaking",
                    "prompt_fr": speaking.prompt_fr,
                    "prompt_target": speaking.prompt_target,
                    "target_seconds": speaking.target_seconds,
                }
        mcq_answered = sum(1 for a in attempt.answers if a["phase"] == "mcq")
        return {
            "id": attempt.id,
            "status": attempt.status,
            "phase": attempt.phase,
            "language": placement_set.language,
            "set": {
                "id": attempt.set_id,
                "version": attempt.set_version,
                "title": placement_set.title,
            },
            "progress": {
                "mcq_answered": mcq_answered,
                "mcq_max": STAIRCASE_MAX_ITEMS,
                "listening_answered": sum(1 for a in attempt.answers if a["phase"] == "listening"),
                "listening_total": len(state.get("listening_ids", []))
                or min(LISTENING_ITEMS, len(placement_set.listening_items)),
                "speaking_answered": sum(1 for a in attempt.answers if a["phase"] == "speaking"),
                "speaking_total": len(state.get("speaking_ids", [])),
            },
            "current_item": current,
            "speaking_feedback": [
                {
                    "item_id": a["item_id"],
                    "transcript": a["transcript"],
                    "estimated_level": a["estimated_level"],
                    "confidence": a["confidence"],
                    "observations": a["observations"],
                }
                for a in attempt.answers
                if a["phase"] == "speaking"
            ]
            if attempt.status == "completed"
            else [],
            "result": attempt.result,
            "created_at": attempt.created_at.isoformat(),
            "ended_at": attempt.ended_at.isoformat() if attempt.ended_at else None,
        }

    @staticmethod
    def _mcq(placement_set: PlacementSetVersion, item_id: str) -> McqItem:
        for item in placement_set.mcq_items:
            if item.id == item_id:
                return item
        raise NotFoundError("Question inconnue")

    @staticmethod
    def _listening(placement_set: PlacementSetVersion, item_id: str) -> ListeningItem:
        for item in placement_set.listening_items:
            if item.id == item_id:
                return item
        raise NotFoundError("Item d'écoute inconnu")

    @staticmethod
    def _speaking(placement_set: PlacementSetVersion, item_id: str) -> SpeakingItem:
        for item in placement_set.speaking_items:
            if item.id == item_id:
                return item
        raise NotFoundError("Consigne orale inconnue")
