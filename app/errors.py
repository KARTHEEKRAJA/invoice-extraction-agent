"""Error classification. No third-party imports on purpose.

This module decides whether a failure is worth retrying. It deliberately
imports nothing beyond the standard library so the tests that cover it
run without LangChain, without an API key, and without a network.

The distinction it draws:

  Permanent - a property of the account or the request that a second
              identical call cannot change. Billing, auth, permissions.
              Retrying burns a call to receive the same error.

  Transient - rate limits, overload, timeouts, and malformed model
              output. A retry has a real chance of succeeding.
"""

from __future__ import annotations


class PermanentError(Exception):
    """A failure that retrying cannot fix."""


_PERMANENT_MARKERS = (
    # Anthropic
    "credit balance is too low",
    "invalid x-api-key",
    "authentication_error",
    "permission_error",
    # Google Gemini
    "api key not valid",
    "api_key_invalid",
    "permission_denied",
    "caller does not have permission",
    "has not been used in project",
    "quota exceeded",
    "resource_exhausted",
    # OpenAI
    "incorrect api key provided",
    "insufficient_quota",
    "exceeded your current quota",
    # Generic
    "your account is not authorized",
    "billing",
    # Missing credential entirely - a retry cannot conjure one.
    "an api key is required",
    "api key is required",
)


def classify_error(exc: Exception) -> Exception:
    """Return a PermanentError for account-level failures, else the original."""
    message = str(exc).lower()
    if any(marker in message for marker in _PERMANENT_MARKERS):
        return PermanentError(str(exc))
    return exc


def friendly_message(exc: Exception) -> str:
    """Turn an API error into something the client can act on.

    Clients see this text. A raw stack trace tells them nothing useful
    and leaks internal structure.
    """
    raw = str(exc)
    low = raw.lower()

    if "credit balance is too low" in low:
        return (
            "The Anthropic account has no credits. Add credits at "
            "console.anthropic.com under Plans & Billing, or switch to the "
            "Google provider, which has a free tier."
        )
    if "api key not valid" in low or "api_key_invalid" in low:
        return (
            "The Google API key was rejected. Generate a new one at "
            "aistudio.google.com/apikey"
        )
    if "limit: 0" in low:
        return (
            "This project has no free-tier allocation for that model (limit: 0). "
            "It is not exhausted usage - the quota was never granted. Run "
            "`python check_provider.py` to find a model that works, or create a "
            "fresh project at aistudio.google.com and generate a new key there."
        )
    if "quota exceeded" in low or "resource_exhausted" in low:
        return (
            "The provider's quota is exhausted. On the Gemini free tier this "
            "resets daily; otherwise raise the quota or switch provider."
        )
    if "insufficient_quota" in low or "exceeded your current quota" in low:
        return "The OpenAI account is out of quota. Add billing at platform.openai.com."
    if "api key is required" in low:
        return (
            "No API key was supplied. This agent needs a model provider "
            "credential passed per request."
        )
    if "invalid x-api-key" in low or "authentication_error" in low:
        return "The API key was rejected. Check the key is correct and active."
    if "connection refused" in low or "connect call failed" in low:
        return (
            "Could not reach the model. If using Ollama, check it is running: "
            "`ollama serve`"
        )
    if "rate_limit" in low or "429" in low:
        return "Rate limited by the provider. Wait a moment and retry."
    if "overloaded" in low or "529" in low:
        return "The model is temporarily overloaded. Retry shortly."
    if "timeout" in low or "timed out" in low:
        return "The extraction timed out. The document may be too large or complex."
    return f"Extraction failed: {raw}"
