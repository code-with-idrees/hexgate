"""Wire-protocol translation and streaming helpers."""

from hexgate.protocol.anthropic import (
    ANTHROPIC_VERSION,
    anthropic_request_to_openai,
    anthropic_response_to_openai,
    openai_request_to_anthropic,
    openai_response_to_anthropic,
    prompt_text,
)
from hexgate.protocol.openai import iter_sse, sse, text_of
from hexgate.protocol.streams import (
    anthropic_events_to_openai,
    openai_chunks_to_anthropic,
    rewrite_anthropic_stream,
    rewrite_openai_stream,
)

__all__ = [
    "ANTHROPIC_VERSION",
    "anthropic_events_to_openai",
    "anthropic_request_to_openai",
    "anthropic_response_to_openai",
    "iter_sse",
    "openai_chunks_to_anthropic",
    "openai_request_to_anthropic",
    "openai_response_to_anthropic",
    "prompt_text",
    "rewrite_anthropic_stream",
    "rewrite_openai_stream",
    "sse",
    "text_of",
]
