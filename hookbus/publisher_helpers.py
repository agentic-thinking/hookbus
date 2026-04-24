"""
Publisher helpers - shared extraction logic for HookBus publishers.

Every publisher calls extract_reasoning() to produce canonical
(reasoning_content, reasoning_chars) values that land in the
PostLLMCall event's metadata. Consistent shape across providers
is a compliance requirement (EU AI Act Art. 12 traceability).

This module is deliberately dependency-free so every publisher shim
can import it without pulling extra packages. It handles both object
and dict response shapes so SDK version drift does not break us.
"""
from typing import Any, Optional, Tuple


MAX_REASONING_CHARS = 65536
"""Upper bound on reasoning text length stored on an event.

Longer traces are truncated (see truncate_reasoning). Chosen so a full
dense reasoning trace fits under 64 KiB per event. If a real workload
routinely exceeds this, revisit as a wire-format change, not a silent
bump.
"""


def extract_reasoning(
    response: Any, provider: str = "auto"
) -> Tuple[Optional[str], int]:
    """Return (reasoning_content, reasoning_chars) for any LLM response.

    reasoning_content is None when the model genuinely had no reasoning.
    In that case reasoning_chars is 0.

    provider values:
        anthropic, amp, claude_code   -> Claude thinking blocks
        openai_compat, hermes, kimi,
        minimax, zai                  -> choices[0].message.reasoning_content
        openrouter, gemini            -> choices[0].message.reasoning
        auto (default)                -> try all shapes, return first hit

    Never raises. Publishers should be robust against provider response
    variance; a missing field becomes (None, 0), not an exception.
    """
    anthropic_group = ("anthropic", "amp", "claude_code", "auto")
    openai_group = (
        "openai_compat", "hermes", "kimi", "minimax", "zai",
        "openrouter", "gemini", "auto",
    )

    if provider in anthropic_group:
        text = _extract_anthropic_thinking(response)
        if text is not None:
            return _finalise(text)

    if provider in openai_group:
        text = _extract_openai_compat(response)
        if text is not None:
            return _finalise(text)

    return None, 0


def truncate_reasoning(text: str, max_chars: int = MAX_REASONING_CHARS) -> str:
    """Truncate reasoning text with a visible marker so audit logs show
    the trace was clipped rather than silently lost. Callers that may
    hold None should guard before calling."""
    if len(text) <= max_chars:
        return text
    marker = f"\n...[truncated at {max_chars} chars]"
    head = max_chars - len(marker)
    return text[:head] + marker


def _finalise(text: str) -> Tuple[Optional[str], int]:
    text = truncate_reasoning(text)
    return text, len(text)


def _extract_anthropic_thinking(response: Any) -> Optional[str]:
    """Pull joined text from response.content[i].thinking blocks
    (Claude API thinking-blocks shape)."""
    content = _get(response, "content")
    if not content:
        return None
    parts = []
    for block in content:
        btype = _get(block, "type")
        if btype != "thinking":
            continue
        text = _get(block, "thinking")
        if text:
            parts.append(text)
    return "\n".join(parts) if parts else None


def _extract_openai_compat(response: Any) -> Optional[str]:
    """Pull reasoning from choices[0].message across every OpenAI-compat
    dialect we currently see on the bus (Kimi, MiniMax, Z.AI, OpenRouter,
    Gemini)."""
    choices = _get(response, "choices")
    if not choices:
        return None
    first = choices[0]
    msg = _get(first, "message")
    if msg is None:
        return None

    # Primary field used by Kimi, MiniMax, Z.AI GLM.
    text = _get(msg, "reasoning_content")
    if text:
        return text

    # Fallback used by OpenRouter and Gemini.
    text = _get(msg, "reasoning")
    if text:
        return text

    # MiniMax streaming shape: array of typed reasoning blocks.
    details = _get(msg, "reasoning_details")
    if details and isinstance(details, list):
        parts = [t for t in (_get(d, "text") for d in details) if t]
        return "\n".join(parts) if parts else None

    return None


def _get(obj: Any, key: str) -> Any:
    """Read `key` off an object or dict, uniformly.

    SDK responses ship as typed objects in some versions and plain dicts
    in others (notably after a JSON round-trip). One accessor covers both.
    """
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)
