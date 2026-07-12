"""Gemma calls via the google-genai SDK (Gemini API).

The SDK reads GEMINI_API_KEY from the environment on its own. Both pipeline
stages go through generate() so retry and deadline handling live in one place.

Gemma 4 on the Gemini API only exposes two thinkingLevel values: "minimal"
(no reasoning tokens) and "high" (full reasoning, 15-40+ seconds even on
short text-only prompts). There is no bounded middle ground, so callers pick
a level rather than a token budget.
"""

import logging
import threading
import time

from google import genai
from google.genai import errors, types

log = logging.getLogger("fourfaced.llm")

RETRYABLE_CODES = {429, 500, 502, 503, 504}
# The API rejects request deadlines under 10 seconds.
MIN_TIMEOUT_S = 10.0
# Cap any single attempt so one hung request can't eat the whole clip budget
# and starve the remaining retries.
MAX_ATTEMPT_TIMEOUT_S = 60.0
# Attempts are bounded by the clip deadline, not this number — it only caps
# pathological fast-failure loops. Transient 500s return in ~1-2s, so a
# typical grounding window fits 4-5 attempts.
MAX_ATTEMPTS = 5
# Same-provider last resort: when the primary model exhausts every attempt,
# one shot on the smaller Gemma (separate rate-limit bucket, same API key)
# before the caller's template/generic fallbacks take over.
FALLBACK_MODEL = "gemma-4-26b-a4b-it"

# --- API health tracking (read by the results UI; never blocks calls) ---
_health_lock = threading.Lock()
_health = {
    "consecutive_failures": 0,    # failed attempts since the last success
    "last_call_exhausted": False,  # a whole generate() ran out of retries
    "rate_limited": False,         # a 429 appeared in the current streak
    "last_error": None,
    "last_error_ts": None,
    "last_success_ts": None,
    "active_model": None,          # model being attempted right now, if any
    "last_served_model": None,     # model that produced the last success
}


def _set_active(model):
    with _health_lock:
        _health["active_model"] = model


def last_served_model():
    """Model id that produced the most recent successful generate().

    Read it immediately after a successful call to attribute that call —
    pipeline runs are sequential (and the UI serializes them), so there is
    no cross-call race in practice.
    """
    with _health_lock:
        return _health["last_served_model"]


def _health_failure(message: str, code=None):
    with _health_lock:
        _health["consecutive_failures"] += 1
        _health["last_error"] = message
        _health["last_error_ts"] = time.time()
        if code == 429:
            _health["rate_limited"] = True


def _health_exhausted():
    with _health_lock:
        _health["last_call_exhausted"] = True


def _health_success(model=None):
    with _health_lock:
        _health["consecutive_failures"] = 0
        _health["last_call_exhausted"] = False
        _health["rate_limited"] = False
        _health["last_success_ts"] = time.time()
        _health["active_model"] = None
        if model:
            _health["last_served_model"] = model


def health_snapshot() -> dict:
    """Derived API health for the results UI.

    'degraded' requires more than one isolated retry: a fully exhausted
    call, or 3+ consecutive failed attempts. 'limited' is degraded with a
    429 in the streak. Any successful attempt resets to 'ok'.
    """
    with _health_lock:
        h = dict(_health)
    degraded = h["last_call_exhausted"] or h["consecutive_failures"] >= 3
    status = "limited" if degraded and h["rate_limited"] else \
             "degraded" if degraded else "ok"
    now = time.time()
    return {
        "status": status,
        "active_model": h["active_model"],
        "last_served_model": h["last_served_model"],
        "consecutive_failures": h["consecutive_failures"],
        "last_error": h["last_error"],
        "seconds_since_error":
            round(now - h["last_error_ts"], 1) if h["last_error_ts"] else None,
        "seconds_since_success":
            round(now - h["last_success_ts"], 1) if h["last_success_ts"] else None,
    }


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

    Retries transient errors while budget remains. think=False sets
    thinkingLevel=minimal so no reasoning tokens are produced.
    """
    request_contents = [types.Part(text=c) if isinstance(c, str) else c for c in contents]
    thinking_config = types.ThinkingConfig(thinking_level="high" if think else "minimal")

    last_error = None
    attempts = 0
    while attempts < MAX_ATTEMPTS:
        remaining = deadline - time.monotonic()
        # The API enforces a minimum 10s request deadline, so any attempt with
        # less budget than that could overshoot the clip deadline — refuse it
        # and let the caller's fallbacks (which are local and instant) run.
        if remaining < MIN_TIMEOUT_S:
            break
        attempts += 1
        _set_active(model)
        # If the window still fits two attempts, give this one only half of
        # it, so a hung request leaves room for a retry instead of consuming
        # the whole budget on one roll of the dice.
        if remaining >= 2 * MIN_TIMEOUT_S + 1.0:
            per_attempt = remaining / 2
        else:
            per_attempt = remaining - 0.5
        timeout = min(MAX_ATTEMPT_TIMEOUT_S, max(MIN_TIMEOUT_S, per_attempt))
        try:
            resp = client().models.generate_content(
                model=model,
                contents=request_contents,
                config=types.GenerateContentConfig(
                    temperature=temperature,
                    max_output_tokens=max_tokens,
                    thinking_config=thinking_config,
                    http_options=types.HttpOptions(timeout=int(timeout * 1000)),
                ),
            )
            text = resp.text
            if not text or not text.strip():
                finish = None
                if resp.candidates:
                    finish = resp.candidates[0].finish_reason
                raise LLMError(f"Empty completion from {model} (finish_reason={finish})")
            _health_success(model)
            return text.strip()
        except errors.APIError as e:
            last_error = f"APIError {getattr(e, 'code', '?')}: {str(e)[:200]}"
            if getattr(e, "code", None) not in RETRYABLE_CODES:
                # Client-side error (bad request, auth, ...) — not provider
                # health, so it doesn't feed the health tracker.
                _set_active(None)
                raise LLMError(f"generate({model}) failed: {last_error}") from e
            _health_failure(last_error, getattr(e, "code", None))
            log.warning("Transient LLM error (attempt %d/%d): %s",
                        attempts, MAX_ATTEMPTS, last_error)
        except Exception as e:
            last_error = f"{type(e).__name__}: {str(e)[:200]}"
            _health_failure(last_error)
            log.warning("LLM call failed (attempt %d/%d): %s",
                        attempts, MAX_ATTEMPTS, last_error)
        if attempts < MAX_ATTEMPTS:
            # Short 0.5s/1s/1.5s... backoff ramp, never sleeping past the
            # point where another attempt would still fit in the budget.
            time.sleep(min(0.5 * attempts,
                           max(0.0, deadline - time.monotonic() - MIN_TIMEOUT_S)))

    if attempts == 0:
        # Never reached the API — a budget problem, not a provider-health one.
        raise LLMError(f"generate({model}) not attempted: under "
                       f"{MIN_TIMEOUT_S:.0f}s of clip budget left")

    # Same-provider failover: one attempt on FALLBACK_MODEL with the same
    # prompt and parameters, inside the same clip deadline. Skipped when the
    # budget can no longer fit an attempt, so the caller's local fallbacks
    # still run in time.
    remaining = deadline - time.monotonic()
    if model != FALLBACK_MODEL and remaining >= MIN_TIMEOUT_S:
        log.warning("generate(%s): exhausted after %d attempt(s); failing over "
                    "to %s for one attempt", model, attempts, FALLBACK_MODEL)
        _set_active(FALLBACK_MODEL)
        try:
            resp = client().models.generate_content(
                model=FALLBACK_MODEL,
                contents=request_contents,
                config=types.GenerateContentConfig(
                    temperature=temperature,
                    max_output_tokens=max_tokens,
                    thinking_config=thinking_config,
                    http_options=types.HttpOptions(
                        timeout=int(min(MAX_ATTEMPT_TIMEOUT_S,
                                        max(MIN_TIMEOUT_S, remaining - 0.5)) * 1000)),
                ),
            )
            text = resp.text
            if text and text.strip():
                log.warning("generate(%s): failover to %s SUCCEEDED", model, FALLBACK_MODEL)
                _health_success(FALLBACK_MODEL)
                return text.strip()
            log.warning("generate(%s): failover to %s returned an empty completion",
                        model, FALLBACK_MODEL)
        except Exception as e:
            log.warning("generate(%s): failover to %s failed too: %s: %s",
                        model, FALLBACK_MODEL, type(e).__name__, str(e)[:200])

    _set_active(None)
    _health_exhausted()
    if attempts < MAX_ATTEMPTS:
        log.error("generate(%s): clip budget exhausted after %d attempt(s); "
                  "last error: %s", model, attempts, last_error)
        raise LLMError(f"generate({model}) failed: budget exhausted after "
                       f"{attempts} attempt(s); last error: {last_error}")
    log.error("generate(%s): all %d attempts failed - repeated upstream failure; "
              "last error: %s", model, MAX_ATTEMPTS, last_error)
    raise LLMError(f"generate({model}) failed after {MAX_ATTEMPTS} attempts: {last_error}")
