"""Render a validated invoice into an accounting import file.

Xero and QuickBooks both accept CSV bill imports but disagree on column
names and row semantics. Both expect one row per line item with the
header fields repeated, which is why these look redundant.

Column sets follow each vendor's published bill import template. Verify
against the current template before relying on this in production - both
vendors revise them.
"""

from __future__ import annotations

import csv
import io
from decimal import Decimal
from typing import Callable

from .schemas import Invoice


def _fmt_date(value, fmt: str = "%d/%m/%Y") -> str:
    return value.strftime(fmt) if value else ""


def _fmt_num(value: Decimal | None) -> str:
    return f"{value:.2f}" if value is not None else ""


def to_xero_csv(inv: Invoice, date_format: str = "%d/%m/%Y") -> str:
    """Xero bill import format. One row per line item."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(
        [
            "ContactName", "InvoiceNumber", "InvoiceDate", "DueDate",
            "Description", "Quantity", "UnitAmount", "LineAmount",
            "TaxAmount", "Currency",
        ]
    )

    issue = _fmt_date(inv.issue_date, date_format)
    due = _fmt_date(inv.due_date, date_format)

    items = inv.line_items or [
        # No itemisation: emit a single synthetic line so the import
        # still balances to the invoice total.
        type("_Line", (), {
            "description": "Invoice total",
            "quantity": Decimal("1"),
            "unit_price": inv.subtotal or inv.total,
            "amount": inv.subtotal or inv.total,
        })()
    ]

    for index, item in enumerate(items):
        writer.writerow(
            [
                inv.vendor_name,
                inv.invoice_number,
                issue,
                due,
                item.description,
                _fmt_num(item.quantity) if item.quantity is not None else "1.00",
                _fmt_num(item.unit_price),
                _fmt_num(item.amount),
                # Tax sits on the first row only, or it double-counts.
                _fmt_num(inv.tax_amount) if index == 0 else "",
                inv.currency,
            ]
        )
    return buffer.getvalue()


def to_quickbooks_csv(inv: Invoice, date_format: str = "%m/%d/%Y") -> str:
    """QuickBooks Online bill import format."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(
        [
            "Vendor", "Bill No", "Bill Date", "Due Date",
            "Memo", "Item Description", "Amount", "Currency",
        ]
    )

    issue = _fmt_date(inv.issue_date, date_format)
    due = _fmt_date(inv.due_date, date_format)
    memo = inv.notes or ""

    if inv.line_items:
        for item in inv.line_items:
            writer.writerow(
                [
                    inv.vendor_name, inv.invoice_number, issue, due,
                    memo, item.description, _fmt_num(item.amount), inv.currency,
                ]
            )
        if inv.tax_amount:
            writer.writerow(
                [
                    inv.vendor_name, inv.invoice_number, issue, due,
                    memo, "Tax", _fmt_num(inv.tax_amount), inv.currency,
                ]
            )
    else:
        writer.writerow(
            [
                inv.vendor_name, inv.invoice_number, issue, due,
                memo, "Invoice total", _fmt_num(inv.total), inv.currency,
            ]
        )
    return buffer.getvalue()


def to_generic_csv(inv: Invoice, date_format: str = "%Y-%m-%d") -> str:
    """Flat format for clients who use neither Xero nor QuickBooks."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(
        [
            "vendor_name", "vendor_tax_id", "invoice_number", "issue_date",
            "due_date", "currency", "description", "quantity",
            "unit_price", "line_amount", "subtotal", "tax_amount", "total",
        ]
    )

    issue = _fmt_date(inv.issue_date, date_format)
    due = _fmt_date(inv.due_date, date_format)
    base = [inv.vendor_name, inv.vendor_tax_id or "", inv.invoice_number, issue, due, inv.currency]

    if inv.line_items:
        for index, item in enumerate(inv.line_items):
            writer.writerow(
                base
                + [
                    item.description,
                    _fmt_num(item.quantity),
                    _fmt_num(item.unit_price),
                    _fmt_num(item.amount),
                    _fmt_num(inv.subtotal) if index == 0 else "",
                    _fmt_num(inv.tax_amount) if index == 0 else "",
                    _fmt_num(inv.total) if index == 0 else "",
                ]
            )
    else:
        writer.writerow(
            base
            + ["Invoice total", "", "", _fmt_num(inv.total),
               _fmt_num(inv.subtotal), _fmt_num(inv.tax_amount), _fmt_num(inv.total)]
        )
    return buffer.getvalue()


FORMATTERS: dict[str, Callable[..., str]] = {
    "xero": to_xero_csv,
    "quickbooks": to_quickbooks_csv,
    "generic": to_generic_csv,
}

DEFAULT_DATE_FORMATS = {
    "xero": "%d/%m/%Y",
    "quickbooks": "%m/%d/%Y",
    "generic": "%Y-%m-%d",
}


def render(inv: Invoice, target: str = "generic", date_format: str | None = None) -> str:
    """Render to the named accounting format."""
    key = (target or "generic").lower()
    if key not in FORMATTERS:
        raise ValueError(f"Unknown format {target!r}. Choose from {sorted(FORMATTERS)}.")
    fmt = date_format or DEFAULT_DATE_FORMATS[key]
    return FORMATTERS[key](inv, fmt)
