"""Model providers behind one interface.

The graph should not know or care which vendor answers the call. It hands
over a document and a credential and receives a validated Invoice. That
indirection buys three things:

  1. Development on a free tier, production on whatever the client pays for.
  2. Clients bring the provider they already have a contract with, which
     matters on a multi-tenant marketplace where you never hold the key.
  3. A provider outage is a config change, not a rewrite.

Adding a provider means adding one entry to PROVIDERS. Nothing else moves.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Callable, Protocol

logger = logging.getLogger(__name__)


class ChatModel(Protocol):
    """The slice of the LangChain chat interface this agent uses."""

    def with_structured_output(self, schema): ...
    def invoke(self, messages): ...


@dataclass(frozen=True)
class ProviderSpec:
    """Everything that differs between one vendor and another."""

    name: str
    default_model: str
    env_var: str
    key_url: str
    supports_vision: bool
    builder: Callable[..., ChatModel]
    free_tier: bool = False


# --- Builders -------------------------------------------------------------
# Each is imported lazily inside its own function. A user running Gemini
# should not need the Anthropic package installed, and vice versa.


def _build_google(api_key: str, model: str, temperature: float, timeout: int):
    from langchain_google_genai import ChatGoogleGenerativeAI

    return ChatGoogleGenerativeAI(
        model=model,
        google_api_key=api_key,
        temperature=temperature,
        timeout=timeout,
        max_retries=2,
        max_output_tokens=4096,
    )


def _build_anthropic(api_key: str, model: str, temperature: float, timeout: int):
    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(
        model=model,
        api_key=api_key,
        temperature=temperature,
        timeout=timeout,
        max_retries=2,
        max_tokens=4096,
    )


def _build_openai(api_key: str, model: str, temperature: float, timeout: int):
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=model,
        api_key=api_key,
        temperature=temperature,
        timeout=timeout,
        max_retries=2,
    )


def _build_ollama(api_key: str, model: str, temperature: float, timeout: int):
    """Local models. The api_key argument is ignored - there is no key."""
    from langchain_ollama import ChatOllama

    return ChatOllama(
        model=model,
        temperature=temperature,
        base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
    )


PROVIDERS: dict[str, ProviderSpec] = {
    "google": ProviderSpec(
        name="google",
        # An alias, not a pinned version. Google routes it to whichever
        # Flash model the project has quota for, which is why it works on
        # projects where pinned names return `limit: 0`. Trade-off: the
        # model behind it can change without notice, so pin a specific
        # version before production once you know which one you have.
        default_model="gemini-flash-latest",
        env_var="GOOGLE_API_KEY",
        key_url="https://aistudio.google.com/apikey",
        supports_vision=True,
        builder=_build_google,
        free_tier=True,
    ),
    "anthropic": ProviderSpec(
        name="anthropic",
        default_model="claude-sonnet-4-6",
        env_var="ANTHROPIC_API_KEY",
        key_url="https://console.anthropic.com",
        supports_vision=True,
        builder=_build_anthropic,
    ),
    "openai": ProviderSpec(
        name="openai",
        default_model="gpt-4o-mini",
        env_var="OPENAI_API_KEY",
        key_url="https://platform.openai.com/api-keys",
        supports_vision=True,
        builder=_build_openai,
    ),
    "ollama": ProviderSpec(
        name="ollama",
        default_model="llama3.2-vision",
        env_var="OLLAMA_MODEL",  # no key; kept for interface symmetry
        key_url="https://ollama.com/download",
        supports_vision=True,
        builder=_build_ollama,
        free_tier=True,
    ),
}

DEFAULT_PROVIDER = os.getenv("LLM_PROVIDER", "google").lower()


def get_spec(provider: str | None = None) -> ProviderSpec:
    """Look up a provider, with a clear error listing the valid names."""
    key = (provider or DEFAULT_PROVIDER).lower()
    if key not in PROVIDERS:
        raise ValueError(
            f"Unknown provider {key!r}. Available: {', '.join(sorted(PROVIDERS))}"
        )
    return PROVIDERS[key]


def build(
    *,
    api_key: str,
    provider: str | None = None,
    model: str | None = None,
    temperature: float = 0.0,
    timeout: int = 90,
) -> ChatModel:
    """Construct a per-request model client.

    The key is an argument, never read from module scope. Two tenants
    running concurrently get two clients built from two keys.
    """
    spec = get_spec(provider)

    if not api_key and spec.name != "ollama":
        raise ValueError(
            f"An API key is required for {spec.name}. Get one at {spec.key_url}"
        )

    chosen = model or spec.default_model
    logger.info("Using %s / %s", spec.name, chosen)
    return spec.builder(api_key, chosen, temperature, timeout)


def resolve_key(provider: str | None = None, explicit: str | None = None) -> str:
    """Find a key for local development.

    Order: explicit argument, then the provider's env var. In multi-tenant
    deployment only the explicit path is used - see api.py, which disables
    the env fallback entirely when MULTI_TENANT is true.
    """
    if explicit:
        return explicit
    spec = get_spec(provider)
    if spec.name == "ollama":
        return ""  # genuinely keyless
    return os.getenv(spec.env_var, "")
