from abc import ABC, abstractmethod

from disha.llm.schemas import LLMRequest, LLMResponse


class LLMProvider(ABC):
    """Stateless adapter for one provider. The router owns retries + audit logging."""

    name: str

    @abstractmethod
    async def complete(
        self,
        request: LLMRequest,
        model: str,
        api_key: str,
    ) -> LLMResponse: ...
