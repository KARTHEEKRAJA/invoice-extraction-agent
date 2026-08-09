"""Run one invoice through the agent from the command line.

Usage:
    $env:GOOGLE_API_KEY = "..."
    python run_local.py test-invoices\\01_clean_australian.pdf

    # explicit provider
    python run_local.py invoice.pdf --provider anthropic --format xero

Useful for checking extraction quality against real documents before
you touch the HTTP layer.
"""

import argparse
import json
import mimetypes
import sys
import time
from pathlib import Path

from app.graph import run
from app.providers import DEFAULT_PROVIDER, PROVIDERS, get_spec, resolve_key


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract one invoice.")
    parser.add_argument("file", type=Path, help="PDF, PNG, or JPEG invoice")
    parser.add_argument("--provider", default=DEFAULT_PROVIDER,
                        choices=sorted(PROVIDERS), help="Model provider")
    parser.add_argument("--model", default=None, help="Override the default model")
    parser.add_argument("--format", default="generic",
                        choices=["xero", "quickbooks", "generic"])
    parser.add_argument("--out", type=Path, help="Write the CSV here")
    args = parser.parse_args()

    spec = get_spec(args.provider)
    api_key = resolve_key(args.provider)

    if not api_key and spec.name != "ollama":
        print(f"No {spec.env_var} found.", file=sys.stderr)
        print(f"Get a key at {spec.key_url}", file=sys.stderr)
        print(f'Then: $env:{spec.env_var} = "your-key"', file=sys.stderr)
        return 1

    if not args.file.exists():
        print(f"No such file: {args.file}", file=sys.stderr)
        return 1

    content_type = mimetypes.guess_type(args.file.name)[0] or "application/pdf"

    started = time.perf_counter()
    result, csv_text = run(
        file_bytes=args.file.read_bytes(),
        content_type=content_type,
        api_key=api_key,
        output_format=args.format,
        provider=args.provider,
        model=args.model,
    )
    elapsed = time.perf_counter() - started

    print(f"\n{'=' * 62}")
    print(f"{args.file.name}")
    print(f"Provider: {spec.name}/{args.model or spec.default_model}   "
          f"Mode: {result.source_mode}   Attempts: {result.attempts}   "
          f"{elapsed:.1f}s")
    print(f"{'=' * 62}\n")

    if result.invoice:
        print(json.dumps(result.invoice.model_dump(mode="json"), indent=2))
    else:
        print("EXTRACTION FAILED\n")
        for issue in result.issues:
            print(f"  {issue.message}")
        return 2

    if result.issues:
        print(f"\n--- {len(result.issues)} issue(s) ---")
        for issue in result.issues:
            print(f"  [{issue.severity.upper():7}] {issue.field}: {issue.message}")
        print(f"\nNeeds review: {result.needs_review}")
    else:
        print("\nNo issues found.")

    if csv_text:
        if args.out:
            args.out.write_text(csv_text)
            print(f"\nCSV written to {args.out}")
        else:
            print(f"\n--- CSV ({args.format}) ---")
            print(csv_text)

    return 0


if __name__ == "__main__":
    sys.exit(main())
