"""HTTP surface for the agent.

The client's API key arrives per request via the X-Api-Key header. It is
read into a local variable, passed down through graph state, and never
stored, logged, or cached. This is what "credentials live per user" means
in practice.

For local development only, ANTHROPIC_API_KEY may be used as a fallback.
That fallback is deliberately disabled when MULTI_TENANT=true, which is
how this should run on any shared platform.
"""

from __future__ import annotations

import logging
import os
import time

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from .graph import run
from .ingest import UnsupportedFileError
from .errors import PermanentError, friendly_message
from .providers import DEFAULT_PROVIDER, PROVIDERS, get_spec, resolve_key

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", 10 * 1024 * 1024))
MULTI_TENANT = os.getenv("MULTI_TENANT", "true").lower() == "true"

app = FastAPI(
    title="Invoice Extraction Agent",
    version="1.0.0",
    description="Extracts structured data from invoices and receipts and "
    "emits an accounting-ready CSV.",
)


def _resolve_key(header_key: str | None, provider: str) -> str:
    """Per-request key resolution. Header first, always."""
    spec = get_spec(provider)

    if header_key:
        return header_key

    if spec.name == "ollama":
        return ""  # local model, genuinely keyless

    if not MULTI_TENANT:
        fallback = resolve_key(provider)
        if fallback:
            logger.warning("Using environment API key - single-tenant mode only")
            return fallback

    raise HTTPException(
        status_code=401,
        detail=(
            f"Missing X-Api-Key header. Supply a {spec.name} key per request. "
            f"Get one at {spec.key_url}"
        ),
    )


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "multi_tenant": MULTI_TENANT,
        "default_provider": DEFAULT_PROVIDER,
        "providers": {
            name: {
                "default_model": spec.default_model,
                "vision": spec.supports_vision,
                "free_tier": spec.free_tier,
            }
            for name, spec in PROVIDERS.items()
        },
    }


@app.post("/extract")
async def extract(
    file: UploadFile = File(..., description="Invoice as PDF, PNG, or JPEG"),
    provider: str = Form(DEFAULT_PROVIDER, description="google | anthropic | openai | ollama"),
    model: str | None = Form(None, description="Override the provider default model"),
    output_format: str = Form("generic", description="xero | quickbooks | generic"),
    date_format: str | None = Form(None, description="strftime pattern, e.g. %d/%m/%Y"),
    x_api_key: str | None = Header(None, alias="X-Api-Key"),
):
    """Extract one invoice and return structured data plus CSV."""
    try:
        get_spec(provider)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    api_key = _resolve_key(x_api_key, provider)

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)}MB limit.",
        )

    started = time.perf_counter()
    try:
        result, csv_text = run(
            file_bytes=data,
            content_type=file.content_type or "",
            api_key=api_key,
            output_format=output_format,
            date_format=date_format,
            provider=provider,
            model=model,
        )
    except UnsupportedFileError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    except PermanentError as exc:
        # 402 so the caller can distinguish "your account has a problem"
        # from "the service broke".
        raise HTTPException(status_code=402, detail=friendly_message(exc)) from exc
    except Exception as exc:
        logger.exception("Unhandled failure during extraction")
        raise HTTPException(status_code=500, detail=f"Extraction failed: {exc}") from exc

    elapsed = time.perf_counter() - started
    logger.info("Run completed in %.2fs, needs_review=%s", elapsed, result.needs_review)

    return JSONResponse(
        {
            "success": result.invoice is not None,
            "needs_review": result.needs_review,
            "runtime_seconds": round(elapsed, 2),
            "provider": provider,
            "source_mode": result.source_mode,
            "attempts": result.attempts,
            "invoice": result.invoice.model_dump(mode="json") if result.invoice else None,
            "issues": [i.model_dump() for i in result.issues],
            "csv": csv_text,
        }
    )
