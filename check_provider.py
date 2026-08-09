"""Find out which models your key can actually use.

Gemini free-tier quota is granted per project AND per model, so a key
that returns `limit: 0` on one model may work fine on another. Rather
than guessing, this sends a one-token request to each candidate and
reports what comes back.

Usage:
    python check_provider.py                  # test the default provider
    python check_provider.py --provider all   # test everything configured
"""

import argparse
import os
import sys

# Candidates worth trying, cheapest and most likely to have free quota first.
CANDIDATES = {
    "google": [
        "gemini-2.0-flash",
        "gemini-2.0-flash-lite",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-flash-latest",
        "gemini-1.5-flash",
    ],
    "anthropic": [
        "claude-haiku-4-5-20251001",
        "claude-sonnet-4-6",
    ],
    "openai": [
        "gpt-4o-mini",
        "gpt-4.1-mini",
    ],
    "ollama": [
        "llama3.2-vision",
        "qwen2.5",
    ],
}

PING = "Reply with the single word: ok"


def classify(exc: Exception) -> str:
    """Turn an exception into a short verdict."""
    m = str(exc).lower()
    if "limit: 0" in m:
        return "NO FREE QUOTA  (project not eligible for this model)"
    if "quota" in m or "resource_exhausted" in m or "429" in m:
        return "RATE LIMITED   (quota exists but currently exhausted)"
    if "api key not valid" in m or "api_key_invalid" in m:
        return "BAD KEY"
    if "not found" in m or "404" in m or "does not exist" in m:
        return "MODEL NOT AVAILABLE"
    if "permission" in m or "403" in m:
        return "NO PERMISSION"
    if "credit balance" in m:
        return "NO CREDITS"
    if "connection" in m or "refused" in m:
        return "CANNOT CONNECT  (is the service running?)"
    return f"FAILED - {str(exc)[:70]}"


def test_provider(provider: str) -> list[str]:
    """Try every candidate model for one provider. Returns the working ones."""
    from app.providers import PROVIDERS, get_spec, resolve_key

    spec = get_spec(provider)
    key = resolve_key(provider)

    print(f"\n{'=' * 66}")
    print(f"  {spec.name.upper()}")
    print(f"{'=' * 66}")

    if not key and spec.name != "ollama":
        print(f"  No {spec.env_var} set.")
        print(f"  Get a key: {spec.key_url}")
        print(f'  Then:      $env:{spec.env_var} = "your-key"')
        return []

    if key:
        print(f"  Key: {key[:8]}...{key[-4:]}  ({len(key)} chars)")

    working = []
    for model in CANDIDATES.get(provider, []):
        sys.stdout.write(f"  {model:<28} ")
        sys.stdout.flush()
        try:
            from app.providers import build

            llm = build(api_key=key, provider=provider, model=model, timeout=30)
            llm.invoke(PING)
            print("WORKS")
            working.append(model)
        except Exception as exc:
            print(classify(exc))

    return working


def main() -> int:
    parser = argparse.ArgumentParser(description="Check which models your keys can use.")
    parser.add_argument("--provider", default=None,
                        help="google | anthropic | openai | ollama | all")
    args = parser.parse_args()

    from app.providers import DEFAULT_PROVIDER, PROVIDERS

    targets = (
        sorted(PROVIDERS) if args.provider == "all"
        else [args.provider or DEFAULT_PROVIDER]
    )

    results: dict[str, list[str]] = {}
    for provider in targets:
        try:
            results[provider] = test_provider(provider)
        except ImportError as exc:
            print(f"\n  {provider}: package not installed - {exc}")
            results[provider] = []

    print(f"\n{'=' * 66}")
    print("  SUMMARY")
    print(f"{'=' * 66}")

    any_working = False
    for provider, models in results.items():
        if models:
            any_working = True
            print(f"  {provider}: {', '.join(models)}")
            print(f"\n  Run with:")
            print(f"    python run_local.py test-invoices\\01_clean_australian.pdf "
                  f"--provider {provider} --model {models[0]}")
        else:
            print(f"  {provider}: nothing usable")

    if not any_working:
        print("\n  No working models found. Options:")
        print("    1. Create a NEW project at aistudio.google.com and a fresh key")
        print("       (keys made inside an existing GCP project often get limit: 0)")
        print("    2. Run locally with Ollama - genuinely free, no key:")
        print("       ollama serve  &&  ollama pull llama3.2-vision")
        print("       pip install langchain-ollama")
        print("    3. Put $5 of credits on console.anthropic.com")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
