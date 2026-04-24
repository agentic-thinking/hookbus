"""
Publisher helpers - shared extraction logic for HookBus publishers.

Every publisher calls extract_reasoning() to produce canonical
(reasoning_content, reasoning_chars, reply_text) values that land in the
PostLLMCall event's metadata. Consistent shape across providers is a
compliance requirement (EU AI Act Art. 12 traceability).

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
) -> Tuple[Optional[str], int, str]:
    """Return (reasoning_content, reasoning_chars, reply_text) for any LLM response.

    reasoning_content is None when the model genuinely had no reasoning field
    on the response. It is the empty string "" when the field was present but
    empty - a meaningful signal that the model chose not to emit reasoning.
    reasoning_chars reflects the original length before any truncation.

    reply_text is the final user-facing response text extracted from the same
    envelope. Always a string; empty when the response carried no reply content.

    provider values:
        anthropic, amp, claude_code   -> Claude thinking blocks
        openai_compat, hermes, kimi,
        minimax, zai                  -> choices[0].message.reasoning_content
        openrouter, gemini            -> choices[0].message.reasoning
        agents_sdk                    -> ModelResponse.output_text / .output
        auto (default)                -> try all shapes, return first hit

    Does not intentionally raise for missing fields. Publishers should be robust against provider response
    variance; a missing field becomes (None, 0, ""), not an exception.
    """
    anthropic_group = ("anthropic", "amp", "claude_code", "auto")
    openai_group = (
        "openai_compat", "hermes", "kimi", "minimax", "zai",
        "openrouter", "gemini", "auto",
    )
    agents_sdk_group = ("agents_sdk", "auto")

    reply = _extract_reply(response)

    if provider in anthropic_group:
        text = _extract_anthropic_thinking(response)
        if text is not None:
            reasoning, chars = _finalise(text)
            return reasoning, chars, reply

    if provider in openai_group:
        text = _extract_openai_compat(response)
        if text is not None:
            reasoning, chars = _finalise(text)
            return reasoning, chars, reply

    if provider in agents_sdk_group:
        text = _extract_agents_sdk(response)
        if text is not None:
            reasoning, chars = _finalise(text)
            return reasoning, chars, reply

    return None, 0, reply


def truncate_reasoning(text: str, max_chars: int = MAX_REASONING_CHARS) -> str:
    """Truncate reasoning text with a visible marker so audit logs show
    the trace was clipped rather than silently lost. Callers that may
    hold None should guard before calling."""
    if len(text) <= max_chars:
        return text
    marker = f"\n...[truncated at {max_chars} chars]"
    head = max_chars - len(marker)
    return text[:head] + marker


def _finalise(text: str) -> Tuple[str, int]:
    """Return (possibly-truncated text, original length). Original length is
    used for reasoning_chars so clipped traces still report true size."""
    original_len = len(text)
    return truncate_reasoning(text), original_len


def _extract_reply(response: Any) -> str:
    """Pull the user-facing reply from any supported response shape.
    Always returns a string; empty when nothing could be located."""
    # OpenAI-compat chat completion.
    choices = _get(response, "choices")
    if choices:
        msg = _get(choices[0], "message")
        if msg is not None:
            content = _get(msg, "content")
            if isinstance(content, str):
                return content
            # Some providers return content as a list of blocks.
            if isinstance(content, list):
                parts = []
                for blk in content:
                    t = _get(blk, "text")
                    if isinstance(t, str) and t:
                        parts.append(t)
                if parts:
                    return "\n".join(parts)

    # Anthropic Messages API: content[] with type=='text'.
    content = _get(response, "content")
    if isinstance(content, list):
        parts = []
        for block in content:
            if _get(block, "type") == "text":
                t = _get(block, "text")
                if isinstance(t, str) and t:
                    parts.append(t)
        if parts:
            return "\n".join(parts)

    # OpenAI Agents SDK ModelResponse: .output_text shortcut.
    out_text = _get(response, "output_text")
    if isinstance(out_text, str) and out_text:
        return out_text

    # OpenAI Agents SDK ModelResponse: .output list of output items.
    output_items = _get(response, "output")
    if isinstance(output_items, list):
        parts = []
        for item in output_items:
            t = _get(item, "text") or _get(item, "content")
            if isinstance(t, str) and t:
                parts.append(t)
            elif isinstance(t, list):
                for blk in t:
                    bt = _get(blk, "text")
                    if isinstance(bt, str) and bt:
                        parts.append(bt)
        if parts:
            return "\n".join(parts)

    return ""


def _extract_anthropic_thinking(response: Any) -> Optional[str]:
    """Pull joined text from response.content[i].thinking blocks
    (Claude API thinking-blocks shape).

    Returns None when no thinking block is present; returns the (possibly
    empty) joined text when at least one thinking block was emitted - an
    explicit empty reasoning is meaningful, treat it differently from
    absent."""
    content = _get(response, "content")
    if not content:
        return None
    parts = []
    found_any_thinking_block = False
    for block in content:
        btype = _get(block, "type")
        if btype != "thinking":
            continue
        found_any_thinking_block = True
        text = _get(block, "thinking")
        if text is not None:
            parts.append(text)
    if not found_any_thinking_block:
        return None
    return "\n".join(parts)


def _extract_openai_compat(response: Any) -> Optional[str]:
    """Pull reasoning from choices[0].message across every OpenAI-compat
    dialect we currently see on the bus (Kimi, MiniMax, Z.AI, OpenRouter,
    Gemini).

    Uses `is not None` rather than truthy checks so an explicit empty-string
    reasoning is returned as "" rather than falling through to the next
    field. Empty-but-present reasoning is a signal the model chose not to
    reason; absent reasoning means the provider does not expose it at all.
    """
    choices = _get(response, "choices")
    if not choices:
        return None
    first = choices[0]
    msg = _get(first, "message")
    if msg is None:
        return None

    # Primary field used by Kimi, MiniMax, Z.AI GLM.
    text = _get(msg, "reasoning_content")
    if text is not None:
        return text

    # Fallback used by OpenRouter and Gemini.
    text = _get(msg, "reasoning")
    if text is not None:
        return text

    # MiniMax streaming shape: array of typed reasoning blocks.
    details = _get(msg, "reasoning_details")
    if details and isinstance(details, list):
        parts = [t for t in (_get(d, "text") for d in details) if t is not None]
        if parts:
            return "\n".join(parts)

    return None


def _extract_agents_sdk(response: Any) -> Optional[str]:
    """OpenAI Agents SDK ModelResponse does not expose reasoning separately
    from reply - the SDK's abstraction flattens them. Return None so the
    caller knows to rely on reply_text alone for this shape."""
    # Intentionally minimal: Agents SDK leaves reasoning on the underlying
    # provider response, which is accessible via .response or .raw_response
    # on some versions. When upstream stabilises the surface area, wire it
    # here. For now, reply-only.
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
