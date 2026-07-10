"""Gemma calls via the google-genai SDK (Gemini API).

The SDK reads GEMINI_API_KEY from the environment on its own. Both pipeline
stages go through generate() so retry and deadline handling live in one place.

Gemma 4 on the Gemini API always produces internal "thinking" tokens before
its answer and does not accept a thinking config, so max_tokens here must
leave room for thoughts + answer — a cap that is too small yields an empty
response with finish_reason MAX_TOKENS.
"""

import logging
import time

from google import genai
from google.genai import errors, types

log = logging.getLogger("fourfaced.llm")

RETRYABLE_CODES = {429, 500, 502, 503, 504}
# The API rejects request deadlines under 10 seconds.
MIN_TIMEOUT_S = 10.0

# Prefilling the model turn with an empty think block suppresses Gemma 4's
# thinking entirely (the hosted API exposes no thinking config for Gemma;
# this mirrors the chat template's enable_thinking=False mechanism).
NO_THINK_PREFILL = "<|think|><|/think|>"

_client = None


def client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client()
    return _client


class LLMError(Exception):
    pass


def generate(model: str, contents, deadline: float, max_tokens: int = 2048,
             temperature: float = 0.7, think: bool = True) -> str:
    """One generate_content call, bounded by `deadline` (time.monotonic secs).

    Retries transient errors while budget remains. think=False suppresses
    Gemma's reasoning tokens for low-latency calls.
    """
    parts = [types.Part(text=c) if isinstance(c, str) else c for c in contents]
    request_contents = [types.Content(role="user", parts=parts)]
    if not think:
        request_contents.append(
            types.Content(role="model", parts=[types.Part(text=NO_THINK_PREFILL)]))

    last_error = "unknown"
    for attempt in (1, 2, 3):
        remaining = deadline - time.monotonic()
        # The API enforces a minimum 10s request deadline, so any attempt with
        # less budget than that could overshoot the clip deadline — refuse it
        # and let the caller's fallbacks (which are local and instant) run.
        if remaining < MIN_TIMEOUT_S:
            break
        try:
            resp = client().models.generate_content(
                model=model,
                contents=request_contents,
                config=types.GenerateContentConfig(
                    temperature=temperature,
                    max_output_tokens=max_tokens,
                    http_options=types.HttpOptions(
                        timeout=int(max(MIN_TIMEOUT_S, remaining - 0.5) * 1000)),
                ),
            )
            text = resp.text
            if not text or not text.strip():
                finish = None
                if resp.candidates:
                    finish = resp.candidates[0].finish_reason
                raise LLMError(f"Empty completion from {model} (finish_reason={finish})")
            return text.strip()
        except errors.APIError as e:
            last_error = f"APIError {getattr(e, 'code', '?')}: {str(e)[:200]}"
            if getattr(e, "code", None) not in RETRYABLE_CODES:
                raise LLMError(f"generate({model}) failed: {last_error}") from e
            log.warning("Transient LLM error (attempt %d): %s", attempt, last_error)
            time.sleep(min(1.5, max(0.0, deadline - time.monotonic() - MIN_TIMEOUT_S)))
        except Exception as e:
            last_error = f"{type(e).__name__}: {str(e)[:200]}"
            log.warning("LLM call failed (attempt %d): %s", attempt, last_error)
    raise LLMError(f"generate({model}) failed: {last_error}")
