"""One versioned voice configuration, pinned on every session for traceability."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from ari.domain.errors import InvalidStateError


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
            "provider": self.provider,
            "models": _thaw(self.models),
            "parameters": _thaw(self.parameters),
        }

    def resolve_persisted(
        self, stack_id: str, version: str, snapshot: Mapping[str, Any]
    ) -> VoiceStack:
        """A session may only resume on exactly the configuration it was created with."""
        if stack_id != self.id or version != self.version or self.snapshot() != dict(snapshot):
            raise InvalidStateError(
                f"Persisted voice stack {stack_id}@{version} is not available exactly"
            )
        return self
