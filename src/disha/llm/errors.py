class LLMError(Exception):
    """Base for all LLM router/provider failures."""


class AuthError(LLMError):
    """Bad/missing API key. Do not retry."""


class CredentialError(LLMError):
    """Tenant BYOK credential is expired/revoked. Do not retry; surface to user."""


class RateLimitError(LLMError):
    """Provider rate limit hit. Retry with backoff."""


class TransientError(LLMError):
    """5xx, network timeout, etc. Retry with backoff."""


class SchemaError(LLMError):
    """Response didn't parse into the requested output_schema. Do not retry."""


class ContentFilterError(LLMError):
    """Provider refused to complete due to safety filters. Do not retry."""
