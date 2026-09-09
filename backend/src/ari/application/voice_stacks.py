from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from ari.domain.errors import InvalidStateError, NotFoundError
from ari.domain.models import InteractionMode, VoiceProfile


class VoiceTransport(StrEnum):
    REALTIME = "realtime"
    PIPELINE = "pipeline"


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("Voice stack configuration keys must be strings")
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if value is None or isinstance(value, str | int | float | bool):
        return value
    raise TypeError(f"Unsupported voice stack configuration value: {type(value).__name__}")


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class VoiceStack:
    id: str
    version: str
    transport: VoiceTransport
    provider: str
    models: Mapping[str, str]
    parameters: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not self.id or not self.version or not self.provider:
            raise ValueError("Voice stack id, version, and provider must be non-empty")
        if not all(
            isinstance(key, str) and isinstance(value, str) for key, value in self.models.items()
        ):
            raise TypeError("Voice stack models must map strings to strings")
        object.__setattr__(self, "models", _freeze(self.models))
        object.__setattr__(self, "parameters", _freeze(self.parameters))

    def snapshot(self) -> dict[str, object]:
        return {
            "id": self.id,
            "version": self.version,
            "transport": self.transport.value,
            "provider": self.provider,
            "models": _thaw(self.models),
            "parameters": _thaw(self.parameters),
        }


class VoiceStackRegistry:
    """Deterministic voice configuration selected once when a session is created."""

    def __init__(self, stacks: tuple[VoiceStack, ...]) -> None:
        self._stacks = {stack.id: stack for stack in stacks}
        if len(self._stacks) != len(stacks):
            raise ValueError("Voice stack ids must be unique")

    def list(self) -> tuple[VoiceStack, ...]:
        return tuple(self._stacks[stack_id] for stack_id in sorted(self._stacks))

    def get(self, stack_id: str) -> VoiceStack:
        try:
            return self._stacks[stack_id]
        except KeyError as exc:
            raise NotFoundError(f"Unknown voice stack {stack_id}") from exc

    def resolve_persisted(
        self,
        stack_id: str,
        version: str,
        snapshot: Mapping[str, Any],
    ) -> VoiceStack:
        current = self.get(stack_id)
        if current.version != version or current.snapshot() != dict(snapshot):
            raise InvalidStateError(
                f"Persisted voice stack {stack_id}@{version} is not available exactly"
            )
        return current

    def default_for(
        self,
        interaction_mode: InteractionMode,
        voice_profile: VoiceProfile,
        *,
        preferred_transport: VoiceTransport,
    ) -> VoiceStack:
        if preferred_transport is VoiceTransport.PIPELINE:
            stack_id = (
                "pipeline_low_latency"
                if interaction_mode is InteractionMode.IMMERSIVE
                else "pipeline_economy"
            )
        else:
            stack_id = (
                "realtime_quality" if voice_profile is VoiceProfile.QUALITY else "realtime_economy"
            )
        return self.get(stack_id)

    def pipeline_alternative_for(self, interaction_mode: InteractionMode) -> VoiceStack:
        return self.get(
            "pipeline_low_latency"
            if interaction_mode is InteractionMode.IMMERSIVE
            else "pipeline_economy"
        )
