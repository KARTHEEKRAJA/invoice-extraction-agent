"""Invoice Extraction Agent - platform entrypoint.

This module is what a hosting platform imports and calls. It exposes one
callable, `run_agent`, that accepts a file in whatever shape the host
hands over and returns a plain JSON-serialisable dict.

Keeping this separate from `api.py` matters: the FastAPI app is one way
to reach the agent, not the agent itself. A platform that runs the
package directly should not have to spin up an HTTP server to use it.

    from app import run_agent
    result = run_agent(file=raw_bytes, filename="invoice.pdf", api_key="...")
"""

from __future__ import annotations

import base64
import binascii
import mimetypes
from pathlib import Path
from typing import Any

__version__ = "1.0.0"

# Declared here so a platform analyzer reading this package sees the truth:
# the agent calls exactly ONE provider per run, chosen by the client.
SUPPORTED_PROVIDERS = ("google", "anthropic", "openai", "ollama")
PROVIDER_REQUIREMENT = "one_of"

SUPPORTED_INPUT_TYPES = ("application/pdf", "image/png", "image/jpeg", "image/webp")


def _coerce_bytes(value: Any, filename: str | None) -> tuple[bytes, str | None]:
    """Accept a file as bytes, a path, or a base64 string.

    Hosts differ in how they hand over an upload. Rather than demanding
    one shape, normalise whatever arrives.
    """
    if isinstance(value, (bytes, bytearray)):
        return bytes(value), filename

    if isinstance(value, str):
        # A path on disk?
        candidate = Path(value)
        try:
            if candidate.exists() and candidate.is_file():
                return candidate.read_bytes(), filename or candidate.name
        except OSError:
            pass

        # A data URI or bare base64 payload?
        payload = value.split(",", 1)[1] if value.startswith("data:") else value
        try:
            return base64.b64decode(payload, validate=True), filename
        except (binascii.Error, ValueError) as exc:
            raise ValueError(
                "File input must be raw bytes, a readable file path, or base64. "
                "Received a string that is none of these."
            ) from exc

    raise ValueError(f"Unsupported file input type: {type(value).__name__}")


def _guess_content_type(filename: str | None, explicit: str | None) -> str:
    if explicit:
        return explicit
    if filename:
        guessed = mimetypes.guess_type(filename)[0]
        if guessed:
            return guessed
    return "application/pdf"


def run_agent(
    *,
    file: Any = None,
    filename: str | None = None,
    content_type: str | None = None,
    api_key: str = "",
    provider: str | None = None,
    model: str | None = None,
    output_format: str = "generic",
    date_format: str | None = None,
    **_ignored: Any,
) -> dict:
    """Extract one invoice and return a JSON-serialisable result.

    Args:
        file: The invoice as bytes, a file path, or a base64 string.
        filename: Original filename, used to infer the content type.
        content_type: MIME type, if the host already knows it.
        api_key: The client's model provider credential. Per request -
            never read from module scope, never cached between calls.
        provider: One of SUPPORTED_PROVIDERS. Defaults to google.
        model: Override the provider's default model.
        output_format: xero | quickbooks | generic.
        date_format: strftime pattern for dates in the CSV.

    Returns:
        A dict with success, needs_review, the extracted invoice,
        any validation issues, and the rendered CSV.

    Unknown keyword arguments are ignored so a host passing extra
    metadata does not break the call.
    """
    if file is None:
        return {
            "success": False,
            "needs_review": True,
            "invoice": None,
            "issues": [
                {
                    "field": "file",
                    "severity": "error",
                    "message": "No file supplied. This agent needs an invoice or "
                    "receipt as a PDF, PNG, or JPEG.",
                }
            ],
            "csv": None,
        }

    try:
        data, resolved_name = _coerce_bytes(file, filename)
    except ValueError as exc:
        return {
            "success": False,
            "needs_review": True,
            "invoice": None,
            "issues": [{"field": "file", "severity": "error", "message": str(exc)}],
            "csv": None,
        }

    # Imported here, not at module scope: reading package metadata and
    # reporting input errors should not require the whole LangGraph stack.
    from .graph import run
    from .ingest import UnsupportedFileError

    try:
        result, csv_text = run(
            file_bytes=data,
            content_type=_guess_content_type(resolved_name, content_type),
            api_key=api_key,
            output_format=output_format,
            date_format=date_format,
            provider=provider,
            model=model,
        )
    except UnsupportedFileError as exc:
        return {
            "success": False,
            "needs_review": True,
            "invoice": None,
            "issues": [{"field": "file", "severity": "error", "message": str(exc)}],
            "csv": None,
        }

    return {
        "success": result.invoice is not None,
        "needs_review": result.needs_review,
        "source_mode": result.source_mode,
        "attempts": result.attempts,
        "invoice": result.invoice.model_dump(mode="json") if result.invoice else None,
        "issues": [i.model_dump() for i in result.issues],
        "csv": csv_text,
    }


# Common aliases, so a host looking for a conventional name finds one.
main = run_agent
invoke = run_agent
handler = run_agent

__all__ = [
    "run_agent",
    "main",
    "invoke",
    "handler",
    "SUPPORTED_PROVIDERS",
    "PROVIDER_REQUIREMENT",
    "SUPPORTED_INPUT_TYPES",
    "__version__",
]
