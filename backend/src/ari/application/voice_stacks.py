"""One versioned voice configuration, pinned on every session for traceability."""

from __future__ import annotations

import logging
import secrets
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from ari.domain.errors import InvalidStateError

logger = logging.getLogger(__name__)


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


class TrainingVoices:
    """Which training voice stack a new session is created with, drawn once and then pinned.

    `stacks` maps a TTS provider name to its stack. A learner forced to a provider gets it when
    that provider is available; otherwise `default` names the provider everyone gets, and without
    a default the provider is drawn 50/50 at random. The drawn stack is stored on the session
    (`voice_stack_config`), so a resumed session keeps its voice whatever this chooser later does.
    """

    def __init__(
        self,
        stacks: Mapping[str, VoiceStack],
        *,
        default: str | None,
        draw: Callable[[Sequence[str]], str] = secrets.choice,
    ) -> None:
        if not stacks:
            raise ValueError("At least one training voice stack is required")
        if default is not None and default not in stacks:
            raise ValueError(f"The default voice {default!r} has no stack")
        self._stacks = dict(stacks)
        self._default = default
        self._draw = draw

    @classmethod
    def single(cls, stack: VoiceStack) -> TrainingVoices:
        return cls({"default": stack}, default="default")

    @property
    def stacks(self) -> tuple[VoiceStack, ...]:
        return tuple(self._stacks.values())

    @property
    def providers(self) -> tuple[str, ...]:
        return tuple(self._stacks)

    @property
    def default_stack(self) -> VoiceStack:
        return self._stacks[self._default or next(iter(self._stacks))]

    def choose(self, forced: str | None = None) -> VoiceStack:
        if forced is not None:
            if forced in self._stacks:
                return self._stacks[forced]
            # Explicit, never silent: the account asked for a voice that is not configured here.
            logger.warning("Forced training voice %r is not available; using the default", forced)
        if self._default is not None:
            return self._stacks[self._default]
        return self._stacks[self._draw(sorted(self._stacks))]
