"""Tests for the validation layer.

These run with no API key and no network. That is deliberate: the logic
that protects the client from bad data should be verifiable without
spending money or depending on a model's mood.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.schemas import Invoice, LineItem
from app.validate import has_errors, validate


def make_invoice(**overrides) -> Invoice:
    """A clean, internally consistent invoice. Override to break it."""
    defaults = dict(
        vendor_name="Acme Supplies Pty Ltd",
        vendor_tax_id="12 345 678 901",
        invoice_number="INV-2026-0042",
        issue_date=date(2026, 7, 1),
        due_date=date(2026, 7, 31),
        currency="AUD",
        subtotal=Decimal("1000.00"),
        tax_amount=Decimal("100.00"),
        total=Decimal("1100.00"),
        line_items=[
            LineItem(
                description="Consulting",
                quantity=Decimal("10"),
                unit_price=Decimal("100.00"),
                amount=Decimal("1000.00"),
            )
        ],
    )
    defaults.update(overrides)
    return Invoice(**defaults)


def test_clean_invoice_has_no_issues():
    assert validate(make_invoice()) == []


def test_totals_mismatch_is_an_error():
    issues = validate(make_invoice(total=Decimal("1200.00")))
    assert has_errors(issues)
    assert any(i.field == "total" for i in issues)


def test_two_cent_drift_is_tolerated():
    """Real invoices round inconsistently. Do not flag noise."""
    assert not has_errors(validate(make_invoice(total=Decimal("1100.02"))))


def test_line_items_not_summing_to_subtotal_is_an_error():
    issues = validate(
        make_invoice(
            line_items=[
                LineItem(description="Consulting", amount=Decimal("500.00")),
            ]
        )
    )
    assert has_errors(issues)
    assert any(i.field == "subtotal" for i in issues)


def test_quantity_times_price_mismatch_is_a_warning_only():
    issues = validate(
        make_invoice(
            line_items=[
                LineItem(
                    description="Consulting",
                    quantity=Decimal("10"),
                    unit_price=Decimal("50.00"),  # implies 500, line says 1000
                    amount=Decimal("1000.00"),
                )
            ]
        )
    )
    assert not has_errors(issues)
    assert any(i.severity == "warning" for i in issues)


def test_zero_total_is_an_error():
    assert has_errors(validate(make_invoice(
        subtotal=Decimal("0"), tax_amount=Decimal("0"),
        total=Decimal("0"), line_items=[],
    )))


def test_absurd_tax_rate_warns():
    issues = validate(make_invoice(
        subtotal=Decimal("100.00"), tax_amount=Decimal("90.00"),
        total=Decimal("190.00"),
        line_items=[LineItem(description="Item", amount=Decimal("100.00"))],
    ))
    assert any(i.field == "tax_amount" and i.severity == "warning" for i in issues)


def test_due_before_issue_warns():
    issues = validate(make_invoice(due_date=date(2026, 6, 1)))
    assert any(i.field == "due_date" for i in issues)


def test_missing_invoice_number_is_an_error():
    assert has_errors(validate(make_invoice(invoice_number="   ")))


def test_missing_issue_date_warns_but_does_not_block():
    issues = validate(make_invoice(issue_date=None, due_date=None))
    assert not has_errors(issues)
    assert any(i.field == "issue_date" for i in issues)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("$1,100.00", Decimal("1100.00")),
        ("1,100.00", Decimal("1100.00")),
        ("₹1,100.00", Decimal("1100.00")),
        ("1100", Decimal("1100")),
    ],
)
def test_currency_symbols_and_separators_are_stripped(raw, expected):
    """The model returns messy strings. The schema cleans them."""
    inv = make_invoice(total=raw)
    assert inv.total == expected


def test_currency_code_is_normalised():
    assert make_invoice(currency="aud").currency == "AUD"


# --- Error classification -------------------------------------------------
# Regression tests for the retry bug: a billing failure was being retried,
# burning a second API call to receive the identical error.

from app.errors import PermanentError, classify_error, friendly_message


def test_billing_error_is_permanent():
    exc = Exception(
        "Error code: 400 - {'type': 'error', 'error': {'type': "
        "'invalid_request_error', 'message': 'Your credit balance is too low "
        "to access the Anthropic API.'}}"
    )
    assert isinstance(classify_error(exc), PermanentError)


def test_bad_api_key_is_permanent():
    assert isinstance(
        classify_error(Exception("authentication_error: invalid x-api-key")),
        PermanentError,
    )


def test_rate_limit_is_not_permanent():
    """Rate limits clear on their own. Retrying is correct."""
    assert not isinstance(
        classify_error(Exception("rate_limit_error: too many requests")),
        PermanentError,
    )


def test_malformed_output_is_not_permanent():
    assert not isinstance(
        classify_error(Exception("ValidationError: total is not a valid decimal")),
        PermanentError,
    )


def test_billing_message_tells_the_user_what_to_do():
    msg = friendly_message(Exception("Your credit balance is too low"))
    assert "console.anthropic.com" in msg
    assert "traceback" not in msg.lower()


# --- Provider layer -------------------------------------------------------

from app.providers import DEFAULT_PROVIDER, PROVIDERS, get_spec


def test_google_is_the_default_provider():
    assert DEFAULT_PROVIDER == "google"
    assert PROVIDERS["google"].free_tier is True


def test_every_provider_declares_vision_support():
    """Scanned invoices need vision. A provider without it must say so."""
    for name, spec in PROVIDERS.items():
        assert isinstance(spec.supports_vision, bool), name
        assert spec.key_url.startswith("http"), name


def test_unknown_provider_names_the_valid_options():
    import pytest
    with pytest.raises(ValueError) as exc:
        get_spec("definitely-not-a-provider")
    assert "google" in str(exc.value)


def test_gemini_bad_key_is_permanent():
    assert isinstance(
        classify_error(Exception("400 API key not valid. Please pass a valid API key.")),
        PermanentError,
    )


def test_gemini_quota_is_permanent():
    """Free-tier quota does not clear in the seconds a retry would take."""
    assert isinstance(
        classify_error(Exception("429 RESOURCE_EXHAUSTED: Quota exceeded")),
        PermanentError,
    )


def test_gemini_key_message_points_at_aistudio():
    msg = friendly_message(Exception("API key not valid"))
    assert "aistudio.google.com" in msg


# --- Image block shape ----------------------------------------------------
# Getting this wrong is a silent failure: the model never sees the image
# and extracts nothing, with no error to explain why.

def test_anthropic_and_google_image_blocks_differ():
    from app.llm import _image_block

    anthropic = _image_block("QUJD", "anthropic")
    assert anthropic["type"] == "image"
    assert anthropic["source"]["data"] == "QUJD"

    google = _image_block("QUJD", "google")
    assert google["type"] == "image_url"
    assert google["image_url"].startswith("data:image/png;base64,")


def test_limit_zero_is_distinguished_from_exhausted_quota():
    """`limit: 0` means the quota was never granted, not that it ran out.

    Telling a user to 'wait for the daily reset' when they have no
    allocation at all sends them to wait for something that never comes.
    """
    msg = friendly_message(Exception("429 RESOURCE_EXHAUSTED limit: 0, model: gemini-2.0-flash"))
    assert "never granted" in msg
    assert "check_provider" in msg
