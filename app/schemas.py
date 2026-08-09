"""Data contracts for the invoice extraction agent.

Everything the LLM returns is forced through these models. If the model
hallucinates a field or returns a malformed number, Pydantic raises here
rather than letting bad data reach the client's accounting system.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


class LineItem(BaseModel):
    """A single billed line on the invoice."""

    description: str = Field(description="Text description of the goods or service")
    quantity: Optional[Decimal] = Field(
        default=None, description="Units billed. Null if the invoice does not state one."
    )
    unit_price: Optional[Decimal] = Field(
        default=None, description="Price per unit before tax. Null if not stated."
    )
    amount: Decimal = Field(description="Line total for this item, excluding tax")

    @field_validator("quantity", "unit_price", "amount", mode="before")
    @classmethod
    def _clean_number(cls, v):
        """Strip currency symbols and thousands separators before parsing."""
        if v is None or isinstance(v, (int, float, Decimal)):
            return v
        cleaned = str(v).replace(",", "").replace("$", "").replace("£", "")
        cleaned = cleaned.replace("€", "").replace("₹", "").strip()
        return cleaned or None


class Invoice(BaseModel):
    """A complete extracted invoice.

    This is the structured output schema handed to the LLM. Field
    descriptions are part of the prompt, so they are written for the model
    as much as for the reader.
    """

    vendor_name: str = Field(description="Legal or trading name of the party issuing the invoice")
    vendor_tax_id: Optional[str] = Field(
        default=None, description="Vendor ABN, VAT, GSTIN, or EIN if shown"
    )
    vendor_address: Optional[str] = Field(default=None, description="Vendor address if shown")

    customer_name: Optional[str] = Field(
        default=None, description="Name of the party being billed"
    )

    invoice_number: str = Field(description="Invoice or reference number as printed")
    issue_date: Optional[date] = Field(default=None, description="Date the invoice was issued")
    due_date: Optional[date] = Field(default=None, description="Payment due date if stated")

    currency: str = Field(
        default="AUD",
        description="ISO 4217 currency code, e.g. AUD, USD, GBP, INR. Infer from symbols if not stated.",
    )

    subtotal: Optional[Decimal] = Field(default=None, description="Total before tax")
    tax_amount: Optional[Decimal] = Field(default=None, description="Total tax charged")
    total: Decimal = Field(description="Final amount payable including tax")

    line_items: list[LineItem] = Field(
        default_factory=list, description="Every billed line. Empty list if none are itemised."
    )

    notes: Optional[str] = Field(
        default=None, description="Payment terms or other notes worth surfacing"
    )

    @field_validator("subtotal", "tax_amount", "total", mode="before")
    @classmethod
    def _clean_number(cls, v):
        if v is None or isinstance(v, (int, float, Decimal)):
            return v
        cleaned = str(v).replace(",", "").replace("$", "").replace("£", "")
        cleaned = cleaned.replace("€", "").replace("₹", "").strip()
        return cleaned or None

    @field_validator("currency", mode="before")
    @classmethod
    def _normalise_currency(cls, v):
        if not v:
            return "AUD"
        return str(v).strip().upper()[:3]


class ValidationIssue(BaseModel):
    """One problem found during arithmetic or completeness checks."""

    field: str
    severity: Literal["error", "warning"]
    message: str


class ExtractionResult(BaseModel):
    """What the agent returns to the client on a successful run."""

    invoice: Optional[Invoice] = None
    issues: list[ValidationIssue] = Field(default_factory=list)
    needs_review: bool = Field(
        default=False,
        description="True when a human should check the result before it is imported",
    )
    source_mode: Literal["text", "vision", "unknown"] = "unknown"
    attempts: int = 1
