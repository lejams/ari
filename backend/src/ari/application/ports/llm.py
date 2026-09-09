from typing import Protocol, TypeVar

from pydantic import BaseModel

from ari.application.contracts import LLMRequest, ProviderResult

T = TypeVar("T", bound=BaseModel)


class LLMProvider(Protocol):
    async def generate_structured(
        self, request: LLMRequest, response_model: type[T]
    ) -> ProviderResult[T]: ...
