"""The agent as a state machine.

    START -> ingest -> extract -> validate -> (conditional)
                          ^                       |
                          |                       +--> format -> END
                          +-----------------------+
                                 retry once

The conditional edge from `validate` back to `extract` is the reason this
is a graph and not a function call. When arithmetic checks fail, the
failures are fed back into the prompt and the model gets one corrective
attempt. That single loop is what takes accuracy from "usually right" to
"right or explicitly flagged", which matters because Central AI pays
nothing for failed runs and clients import this into their books.

Retries are capped at MAX_ATTEMPTS. An uncapped loop against a metered
API is a way to lose money quickly.
"""

from __future__ import annotations

import logging
from typing import Literal, Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from . import errors as err
from . import ingest as ingest_mod
from . import llm as llm_mod
from . import validate as validate_mod
from .formatters import render
from .schemas import ExtractionResult, Invoice, ValidationIssue

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 2


class AgentState(TypedDict, total=False):
    """State carried between nodes.

    `api_key` lives in state rather than in module scope. This is the
    multi-tenancy requirement: two concurrent runs from two different
    businesses carry two different keys through two different state
    objects, and never share a client.
    """

    # Input
    file_bytes: bytes
    content_type: str
    api_key: str
    provider: Optional[str]
    model: Optional[str]
    output_format: str
    date_format: Optional[str]

    # Working state
    mode: str
    payload: str | list[str]
    invoice: Optional[Invoice]
    issues: list[ValidationIssue]
    attempts: int
    error: Optional[str]
    permanent: bool

    # Output
    csv: Optional[str]
    result: Optional[ExtractionResult]


def ingest_node(state: AgentState) -> dict:
    """Decide text vs vision and prepare the payload."""
    mode, payload = ingest_mod.prepare(state["file_bytes"], state["content_type"])
    logger.info("Ingest chose %s mode", mode)
    return {"mode": mode, "payload": payload, "attempts": 0}


def extract_node(state: AgentState) -> dict:
    """Call the model. On a retry, pass the previous failures back in."""
    attempts = state.get("attempts", 0) + 1
    prior = [i.message for i in state.get("issues", []) if i.severity == "error"]

    try:
        invoice = llm_mod.extract_invoice(
            api_key=state["api_key"],
            mode=state["mode"],
            payload=state["payload"],
            provider=state.get("provider"),
            model=state.get("model"),
            prior_issues=prior if attempts > 1 else None,
        )
        return {"invoice": invoice, "attempts": attempts, "error": None, "permanent": False}

    except err.PermanentError as exc:
        # Account-level problem. One clean line, no traceback, no retry.
        logger.error("Extraction failed permanently: %s", exc)
        return {
            "invoice": None,
            "attempts": attempts,
            "error": err.friendly_message(exc),
            "permanent": True,
        }

    except Exception as exc:
        logger.warning("Extraction attempt %d failed: %s", attempts, exc)
        return {
            "invoice": None,
            "attempts": attempts,
            "error": err.friendly_message(exc),
            "permanent": False,
        }


def validate_node(state: AgentState) -> dict:
    """Run arithmetic and completeness checks."""
    invoice = state.get("invoice")
    if invoice is None:
        return {"issues": []}
    issues = validate_mod.validate(invoice)
    logger.info(
        "Validation found %d issue(s), %d error(s)",
        len(issues),
        sum(1 for i in issues if i.severity == "error"),
    )
    return {"issues": issues}


def route_after_validate(state: AgentState) -> Literal["retry", "format", "fail"]:
    """Decide whether to retry, format, or give up.

    Giving up is a legitimate outcome. Returning a flagged partial result
    beats returning confident wrong numbers, and beats an exception that
    the client sees as a hard failure.
    """
    if state.get("invoice") is None:
        # A billing or auth failure will return the same error next time.
        # Retrying it wastes a call and doubles the client's latency.
        if state.get("permanent"):
            logger.info("Failure is permanent, skipping retry")
            return "fail"
        if state.get("attempts", 0) < MAX_ATTEMPTS:
            return "retry"
        return "fail"

    if validate_mod.has_errors(state.get("issues", [])):
        if state.get("attempts", 0) < MAX_ATTEMPTS:
            logger.info("Errors present, retrying extraction")
            return "retry"
        # Out of retries: hand back what we have, clearly marked.
        logger.info("Errors persist after %d attempts, flagging for review", MAX_ATTEMPTS)
    return "format"


def format_node(state: AgentState) -> dict:
    """Render the CSV and assemble the result."""
    invoice = state["invoice"]
    issues = state.get("issues", [])

    csv_text = render(
        invoice,
        target=state.get("output_format") or "generic",
        date_format=state.get("date_format"),
    )

    result = ExtractionResult(
        invoice=invoice,
        issues=issues,
        needs_review=bool(issues),
        source_mode=state.get("mode", "unknown"),  # type: ignore[arg-type]
        attempts=state.get("attempts", 1),
    )
    return {"csv": csv_text, "result": result}


def fail_node(state: AgentState) -> dict:
    """Terminal node for a genuine extraction failure."""
    message = state.get("error") or "Extraction failed after maximum attempts"
    result = ExtractionResult(
        invoice=None,
        issues=[ValidationIssue(field="document", severity="error", message=message)],
        needs_review=True,
        source_mode=state.get("mode", "unknown"),  # type: ignore[arg-type]
        attempts=state.get("attempts", 0),
    )
    return {"result": result, "csv": None}


def build_graph():
    """Compile the agent graph."""
    builder = StateGraph(AgentState)

    builder.add_node("ingest", ingest_node)
    builder.add_node("extract", extract_node)
    builder.add_node("validate", validate_node)
    builder.add_node("format", format_node)
    builder.add_node("fail", fail_node)

    builder.add_edge(START, "ingest")
    builder.add_edge("ingest", "extract")
    builder.add_edge("extract", "validate")
    builder.add_conditional_edges(
        "validate",
        route_after_validate,
        {"retry": "extract", "format": "format", "fail": "fail"},
    )
    builder.add_edge("format", END)
    builder.add_edge("fail", END)

    return builder.compile()


# Compiled once at import. The graph itself is stateless and safe to
# share; per-request data travels in AgentState, not in the graph.
GRAPH = build_graph()


def run(
    *,
    file_bytes: bytes,
    content_type: str,
    api_key: str,
    output_format: str = "generic",
    date_format: str | None = None,
    provider: str | None = None,
    model: str | None = None,
) -> tuple[ExtractionResult, str | None]:
    """Execute one invoice extraction end to end."""
    final = GRAPH.invoke(
        {
            "file_bytes": file_bytes,
            "content_type": content_type,
            "api_key": api_key,
            "output_format": output_format,
            "date_format": date_format,
            "provider": provider,
            "model": model,
        }
    )
    return final["result"], final.get("csv")
