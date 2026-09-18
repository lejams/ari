"""Request bodies of the back-office API."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from ari.content.domain.accounts import Role
from ari.content.domain.protocol import Decision


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginRequest(ApiModel):
    email: Annotated[str, Field(min_length=3, max_length=200)]
    password: Annotated[str, Field(min_length=1, max_length=1000)]


class SetPasswordRequest(ApiModel):
    password: Annotated[str, Field(min_length=1, max_length=1000)]


class CreateAccountRequest(ApiModel):
    email: Annotated[str, Field(min_length=3, max_length=200)]
    display_name: Annotated[str, Field(min_length=1, max_length=120)]
    roles: Annotated[list[Role], Field(min_length=1)]


class ReviseRequest(ApiModel):
    base_version: Annotated[int, Field(ge=1)]
    base_hash: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    record: dict[str, object]  # Validated as a ProtocolRecord by the route, with a 422 on failure.


class DecisionRequest(ApiModel):
    version: Annotated[int, Field(ge=1)]
    protocol_hash: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    decision: Decision
    notes: Annotated[str, Field(max_length=4000)] = ""
    pii_override_note: Annotated[str, Field(min_length=1, max_length=2000)] | None = None


class SegmentRequest(ApiModel):
    page_from: Annotated[int, Field(ge=1)]
    page_to: Annotated[int, Field(ge=1)]
    start_marker: Annotated[str, Field(min_length=1, max_length=200)]


class SegmentStatusRequest(ApiModel):
    status: Literal["discarded", "pending"]
