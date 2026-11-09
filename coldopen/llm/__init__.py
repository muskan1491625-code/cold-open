"""Model-agnostic LLM layer. Pipeline code calls only ``LLMClient.call``/``acall``."""

from coldopen.llm.provider import LLMClient, LLMError, call, configure

__all__ = ["LLMClient", "LLMError", "call", "configure"]
