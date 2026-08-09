"""The extraction call itself, provider-agnostic.

MULTI-TENANCY NOTE - read before changing anything here.

The API key is a function argument. It is never read from a module-level
environment variable at import time, and no client is cached globally.
Central AI lists "credential design that breaks multi-tenant use" as a
rejection reason, and a shared module-level client is exactly that
failure: two businesses running concurrently would share one credential.

Every request builds its own client from its own key and its own provider.
"""

from __future__ import annotations

import logging

from langchain_core.messages import HumanMessage, SystemMessage

from . import providers
from .errors import PermanentError, classify_error, friendly_message  # noqa: F401
from .schemas import Invoice

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You extract structured data from invoices and receipts.

Rules:
- Transcribe only what the document shows. Never infer or invent a value.
- If a field is genuinely absent, return null rather than guessing.
- Numbers must be plain decimals: no currency symbols, no thousands separators.
- Dates must be ISO format (YYYY-MM-DD). Resolve ambiguous formats using
  other date context in the document; if still ambiguous, prefer DD/MM/YYYY
  for AUD, GBP, INR and EUR documents and MM/DD/YYYY for USD documents.
- `total` is the final amount payable including tax. This is the single
  most important field. Get it right.
- Capture every itemised line, including discounts and credits. A discount
  is a negative amount - keep the sign.
- Read the currency from the document itself. If the notes mention a
  converted or approximate amount in another currency, ignore it: the
  invoice currency is the one the payment is due in.
- If the document is not an invoice or receipt, still return your best
  structured reading rather than refusing.
"""

RETRY_SUFFIX = """
Your previous extraction failed validation with these problems:

{issues}

Re-read the document and correct them. Pay particular attention to the
numeric fields flagged above.
"""


def _image_block(b64: str, provider: str) -> dict:
    """Build an image content block in the shape this provider expects.

    LangChain has been converging on a standard block format, but the
    providers still disagree in practice. Anthropic takes a `source`
    object; Google and OpenAI take a data URI. Getting this wrong is a
    silent failure - the model simply never sees the image.
    """
    if provider == "anthropic":
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": b64},
        }
    # google, openai, ollama
    return {"type": "image_url", "image_url": f"data:image/png;base64,{b64}"}


def _build_content(mode: str, payload: str | list[str], provider: str) -> list[dict]:
    """Assemble the user message content for text or vision mode."""
    if mode == "text":
        return [
            {
                "type": "text",
                "text": f"Extract the invoice below.\n\n<document>\n{payload}\n</document>",
            }
        ]

    blocks: list[dict] = [{"type": "text", "text": "Extract the invoice shown in these images."}]
    for page in payload:  # type: ignore[union-attr]
        blocks.append(_image_block(page, provider))
    return blocks


def extract_invoice(
    *,
    api_key: str,
    mode: str,
    payload: str | list[str],
    provider: str | None = None,
    model: str | None = None,
    prior_issues: list[str] | None = None,
) -> Invoice:
    """Run one extraction attempt and return a validated Invoice.

    Raises if the model's output cannot be coerced into the schema. The
    graph handles that by retrying once with the failure details, unless
    the error is permanent.
    """
    spec = providers.get_spec(provider)

    if mode == "vision" and not spec.supports_vision:
        raise PermanentError(
            f"{spec.name} does not support image input, but this document has no "
            f"text layer. Use a vision-capable provider for scanned invoices."
        )

    llm = providers.build(
        api_key=api_key, provider=spec.name, model=model
    ).with_structured_output(Invoice)

    system = SYSTEM_PROMPT
    if prior_issues:
        system += RETRY_SUFFIX.format(issues="\n".join(f"- {i}" for i in prior_issues))

    messages = [
        SystemMessage(content=system),
        HumanMessage(content=_build_content(mode, payload, spec.name)),
    ]

    try:
        result = llm.invoke(messages)
    except Exception as exc:
        raise classify_error(exc) from exc

    if result is None:
        raise ValueError("The model returned no structured output.")

    logger.info("Extracted invoice %s", getattr(result, "invoice_number", "?"))
    return result  # type: ignore[return-value]
