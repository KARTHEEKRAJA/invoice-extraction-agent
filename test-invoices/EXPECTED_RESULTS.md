# Test Invoice Set — Expected Results

Check the agent's output against this table. The **total** is the field that matters
most — get that wrong and the client's books are wrong.

---

## What each file tests

| # | File | Tests | Path |
|---|---|---|---|
| 01 | `01_clean_australian.pdf` | Baseline. Everything consistent. | text |
| 02 | `02_many_line_items.pdf` | Summing 8 line items correctly | text |
| 03 | `03_us_format.pdf` | USD, MM/DD/YYYY dates, sales tax | text |
| 04 | `04_indian_gst.pdf` | INR, GSTIN, large numbers, IGST | text |
| 05 | `05_minimal_receipt.pdf` | No due date, no vendor tax ID | text |
| 06 | `06_arithmetic_error.pdf` | **Validation must catch this** | text |
| 07 | `07_no_line_items.pdf` | Total only, no itemisation | text |
| 08 | `08_messy_with_discount.pdf` | EUR, negative discount line, FX distractor | text |
| 09 | `09_scanned_no_text_layer.pdf` | **Vision fallback**, rotated + noisy | vision |
| 10 | `10_scanned_receipt.pdf` | Vision, heavier degradation | vision |

Also included: `09_*.jpg` and `10_*.jpg` — same content as direct image uploads,
for testing the image path without a PDF wrapper.

---

## Expected values

### 01 — Clean Australian
```
vendor_name     Southern Cross Consulting Pty Ltd
vendor_tax_id   42 118 337 902
invoice_number  SCC-2026-0318
issue_date      2026-07-14
due_date        2026-08-13
currency        AUD
subtotal        4300.00
tax_amount      430.00
total           4730.00
line_items      2
```
**Expect:** `needs_review: false`, `issues: []`, `attempts: 1`

---

### 02 — Many line items
```
vendor_name     Meridian Office Technology
invoice_number  MOT-88214
subtotal        8966.64
tax_amount      896.66
total           9863.30
line_items      8
```
**Expect:** zero issues. If line items don't sum to subtotal, extraction dropped a row.

---

### 03 — US format
```
vendor_name     Blackwell Analytics LLC
vendor_tax_id   47-3319028
invoice_number  BA-2026-1147
issue_date      2026-06-22    ← NOT 2026-22-06
due_date        2026-07-22
currency        USD
subtotal        12540.00
tax_amount      1096.95
total           13636.95
```
**Key check:** `06/22/2026` must parse as 22 June, not fail. Month-first because USD.

---

### 04 — Indian GST
```
vendor_name     Vantage Softworks Private Limited
vendor_tax_id   36AABCV1234K1ZP
invoice_number  VSPL/2026-27/0412
currency        INR
subtotal        423500.00
tax_amount      76230.00
total           499730.00
```
**Key check:** large numbers with commas parse correctly. `499730.00` not `499.73`.

---

### 05 — Minimal receipt
```
vendor_name     The Grind Coffee Roasters
vendor_tax_id   null
invoice_number  R-40921
due_date        null
total           106.15
```
**Expect:** no errors. Nulls are correct here — the document genuinely lacks these.

---

### 06 — Arithmetic error ⚠️ THE IMPORTANT ONE

This invoice is deliberately wrong:
- Line items sum to **3200.00** but the printed subtotal says **3500.00**
- Subtotal + tax = **3850.00** but the printed total says **3900.00**

**Expect:**
```
needs_review    true
attempts        2          ← the retry loop fired
issues          at least 2 errors
                - subtotal: line items sum to 3200 but subtotal reads 3500
                - total: subtotal 3500 plus tax 350 = 3850 but total reads 3900
```

**If this returns `needs_review: false`, the validation layer is broken.** This is
the single most important test in the set — it proves the agent catches bad data
rather than passing it through.

---

### 07 — No line items
```
vendor_name     Delacroix Legal
invoice_number  DL-2026-0891
subtotal        6800.00
tax_amount      680.00
total           7480.00
line_items      []
```
**Expect:** no errors. Empty line items is valid. CSV should emit one synthetic
"Invoice total" row so the import still balances.

---

### 08 — Messy with discount
```
vendor_name     Nordwind Handels GmbH
vendor_tax_id   DE297445118
invoice_number  NW-2026-04417
currency        EUR           ← NOT AUD
subtotal        4401.00
tax_amount      836.19
total           5237.19
line_items      3, one with amount -489.00
```
**Key checks:**
- The negative discount line is captured as `-489.00`, not dropped or made positive
- Currency is EUR. The notes mention an AUD figure as a distractor — if the agent
  returns AUD or 8650.00, it followed the distractor

---

### 09 / 10 — Scanned

Same content as 01 and 05, but rotated, blurred, noised and JPEG-compressed with
no text layer.

**Expect:**
```
source_mode     vision      ← proves the fallback fired
```
Values should match 01 and 05. Small OCR-style errors in the vendor address are
acceptable; **a wrong total is not**.

---

## Running the set

```powershell
# One at a time
python run_local.py test-invoices\01_clean_australian.pdf --format xero

# The critical one
python run_local.py test-invoices\06_arithmetic_error.pdf

# Vision path
python run_local.py test-invoices\09_scanned_no_text_layer.pdf
```

Batch all ten:

```powershell
Get-ChildItem test-invoices\*.pdf | ForEach-Object {
    Write-Host "`n=== $($_.Name) ===" -ForegroundColor Cyan
    python run_local.py $_.FullName --format generic
}
```

---

## Scoring

| Result | Meaning |
|---|---|
| 8–10 correct totals, 06 flagged | Ship it |
| 6–7 correct | Tighten the prompt in `llm.py` |
| 06 not flagged | Validation bug — fix before anything else |
| Vision files fail | Check `pypdfium2` installed correctly |

Note which files fail and how. That list is what we harden against next.
