"""Check the extraction against itself.

An LLM will happily return a confident, well-formed, wrong number. The
only defence is arithmetic: an invoice is internally redundant, so the
parts must agree with the whole. Where they do not, we flag rather than
silently pass bad data into someone's accounting system.

This module is pure functions over data. No I/O, no LLM, no network.
That makes it trivially unit-testable, which is the point.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from .schemas import Invoice, ValidationIssue

# Invoices round in inconsistent places. A cent or two of drift is normal
# and not worth flagging to a human.
TOLERANCE = Decimal("0.02")


def _close(a: Decimal, b: Decimal, tolerance: Decimal = TOLERANCE) -> bool:
    return abs(a - b) <= tolerance


def check_line_items(inv: Invoice) -> list[ValidationIssue]:
    """Line item amounts should sum to the subtotal."""
    issues: list[ValidationIssue] = []
    if not inv.line_items or inv.subtotal is None:
        return issues

    line_sum = sum((item.amount for item in inv.line_items), Decimal("0"))
    if not _close(line_sum, inv.subtotal):
        issues.append(
            ValidationIssue(
                field="subtotal",
                severity="error",
                message=(
                    f"Line items sum to {line_sum} but subtotal reads {inv.subtotal} "
                    f"(difference {abs(line_sum - inv.subtotal)})"
                ),
            )
        )

    for index, item in enumerate(inv.line_items):
        if item.quantity is not None and item.unit_price is not None:
            expected = item.quantity * item.unit_price
            if not _close(expected, item.amount, TOLERANCE * 2):
                issues.append(
                    ValidationIssue(
                        field=f"line_items[{index}].amount",
                        severity="warning",
                        message=(
                            f"{item.quantity} x {item.unit_price} = {expected}, "
                            f"but the line reads {item.amount}"
                        ),
                    )
                )
    return issues


def check_totals(inv: Invoice) -> list[ValidationIssue]:
    """subtotal + tax should equal total."""
    issues: list[ValidationIssue] = []
    if inv.subtotal is None:
        return issues

    tax = inv.tax_amount or Decimal("0")
    expected = inv.subtotal + tax
    if not _close(expected, inv.total):
        issues.append(
            ValidationIssue(
                field="total",
                severity="error",
                message=(
                    f"Subtotal {inv.subtotal} plus tax {tax} = {expected}, "
                    f"but the total reads {inv.total}"
                ),
            )
        )
    return issues


def check_sanity(inv: Invoice) -> list[ValidationIssue]:
    """Catch values that are structurally possible but obviously wrong."""
    issues: list[ValidationIssue] = []

    if inv.total <= 0:
        issues.append(
            ValidationIssue(
                field="total",
                severity="error",
                message=f"Total is {inv.total}, which is not a payable amount",
            )
        )

    if inv.tax_amount is not None and inv.tax_amount < 0:
        issues.append(
            ValidationIssue(
                field="tax_amount", severity="error", message="Tax amount is negative"
            )
        )

    if inv.tax_amount is not None and inv.subtotal is not None and inv.subtotal > 0:
        rate = inv.tax_amount / inv.subtotal
        if rate > Decimal("0.5"):
            issues.append(
                ValidationIssue(
                    field="tax_amount",
                    severity="warning",
                    message=f"Tax is {rate:.0%} of subtotal, which is unusually high",
                )
            )

    if inv.issue_date and inv.due_date and inv.due_date < inv.issue_date:
        issues.append(
            ValidationIssue(
                field="due_date",
                severity="warning",
                message="Due date falls before the issue date",
            )
        )

    if inv.issue_date and inv.issue_date > date.today() + timedelta(days=1):
        issues.append(
            ValidationIssue(
                field="issue_date",
                severity="warning",
                message=f"Issue date {inv.issue_date} is in the future",
            )
        )

    if len(inv.currency) != 3 or not inv.currency.isalpha():
        issues.append(
            ValidationIssue(
                field="currency",
                severity="warning",
                message=f"{inv.currency!r} is not a valid ISO 4217 code",
            )
        )

    return issues


def check_completeness(inv: Invoice) -> list[ValidationIssue]:
    """Warn on fields that accounting imports usually require."""
    issues: list[ValidationIssue] = []

    if not inv.invoice_number.strip():
        issues.append(
            ValidationIssue(
                field="invoice_number", severity="error", message="Invoice number is missing"
            )
        )

    if not inv.vendor_name.strip():
        issues.append(
            ValidationIssue(
                field="vendor_name", severity="error", message="Vendor name is missing"
            )
        )

    if inv.issue_date is None:
        issues.append(
            ValidationIssue(
                field="issue_date",
                severity="warning",
                message="No issue date found; most accounting imports require one",
            )
        )

    return issues


def validate(inv: Invoice) -> list[ValidationIssue]:
    """Run every check and return the combined issue list."""
    return (
        check_completeness(inv)
        + check_line_items(inv)
        + check_totals(inv)
        + check_sanity(inv)
    )


def has_errors(issues: list[ValidationIssue]) -> bool:
    return any(i.severity == "error" for i in issues)
