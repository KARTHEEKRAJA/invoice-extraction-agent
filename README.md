# Invoice Extraction Agent

Extracts structured data from invoices and receipts (PDF, PNG, JPEG) and emits an
accounting-ready CSV for Xero, QuickBooks, or a generic flat format.

Built with **Python · LangGraph · FastAPI · Docker**.

Provider-agnostic: runs on Google Gemini (free tier), Anthropic, OpenAI, or a
local Ollama model. The graph does not know which.

---

## Why it is built this way

**Arithmetic validation, not vibes.** A language model will return a confident,
well-formed, wrong number. An invoice is internally redundant — line items sum to
the subtotal, subtotal plus tax equals the total — so those relationships are
checked in code. Where they disagree, the result is flagged rather than silently
imported into someone's books.

**A retry loop, which is why this is a graph.** When validation finds arithmetic
errors, the failures are fed back into the prompt and the model gets one
corrective attempt. That conditional edge is the difference between "usually
right" and "right or explicitly flagged".

**Text path with a vision fallback.** Digital PDFs go through `pdfplumber` — fast
and cheap. Scans and photos are rendered to PNG and sent to the vision model.
Choosing automatically is what keeps the failure rate down.

**Per-request credentials.** The API key travels as a function argument through
graph state. There is no module-level client and no import-time environment read.
Two businesses running concurrently never share a credential.

**Swappable providers.** `providers.py` holds one entry per vendor. The graph asks
for a model and gets one. This matters on a multi-tenant marketplace where clients
bring the provider they already pay for — and it means development can run on a
free tier while production runs on whatever the client chose.

---

## Architecture

```
START → ingest → extract → validate → ─┬─ retry ──→ extract
                                       ├─ format ──→ END
                                       └─ fail ────→ END
```

| Node | Responsibility |
|---|---|
| `ingest` | Detect text vs. scanned; extract text or render page images |
| `extract` | Structured LLM call constrained by a Pydantic schema |
| `validate` | Arithmetic, sanity, and completeness checks — pure functions |
| `format` | Render Xero / QuickBooks / generic CSV |
| `fail` | Terminal node returning a flagged result rather than an exception |

```
app/
├── schemas.py      Pydantic contracts — the data shape
├── ingest.py       File → text or images
├── providers.py    Model providers behind one interface
├── llm.py          The extraction call (per-request credentials)
├── errors.py       Permanent vs. transient failure classification
├── validate.py     Arithmetic checks — pure, no I/O, fully tested
├── formatters.py   CSV output for each accounting system
├── graph.py        LangGraph state machine
└── api.py          FastAPI service
```

---

## Getting started

### 1. Requirements

Python 3.11 or newer. Check with `python --version`.

### 2. Clone and create a virtual environment

**Windows (PowerShell)**
```powershell
git clone https://github.com/YOUR_USERNAME/invoice-extraction-agent.git
cd invoice-extraction-agent

python -m venv .venv
.venv\Scripts\Activate.ps1
```

If PowerShell blocks the activation script:
```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

**macOS / Linux**
```bash
git clone https://github.com/YOUR_USERNAME/invoice-extraction-agent.git
cd invoice-extraction-agent

python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

This installs the Google provider by default. To use another, uncomment its line
in `requirements.txt` and reinstall — no code changes needed.

### 4. Verify before spending anything

```bash
pytest tests/ -v
```

28 tests, no API key and no network required. The validation logic that protects
the client is verifiable without a single API call. If these pass, your
environment is correct.

### 5. Get an API key

Google Gemini has a free tier and needs no card: https://aistudio.google.com/apikey

Create the key **in a new project**. Keys generated inside an existing Google Cloud
project frequently land in a zero-quota bucket and return `limit: 0` on every request.

### 6. Set the key

**Never put a real key in a tracked file.** `.env` is gitignored; `.env.example` is
documentation and ships with placeholders only.

**Windows (PowerShell)** — current session:
```powershell
$env:GOOGLE_API_KEY = "your-key-here"
```

**macOS / Linux**
```bash
export GOOGLE_API_KEY="your-key-here"
```

**Persistent** — copy the template and edit the copy, not the original:
```bash
cp .env.example .env      # PowerShell: Copy-Item .env.example .env
```

### 7. Check which models your key can use

```bash
python check_provider.py
```

Gemini quota is granted per project **and** per model, so a key that fails on one
model often works on another. This pings each candidate and reports what works.

If everything returns `NO FREE QUOTA (limit: 0)`, the project has no allocation at
all — create a fresh project in AI Studio, or run locally with Ollama (see below).

### 8. Run it

```bash
python run_local.py test-invoices/01_clean_australian.pdf --format xero
```

Expected: total `4730.00`, no issues, `Attempts: 1`.

Then the invoice with deliberately broken arithmetic:

```bash
python run_local.py test-invoices/06_arithmetic_error.pdf
```

Expected: `needs_review: true`, `attempts: 2`, two errors flagged. This is the test
that proves validation works — anything can parse a clean invoice.

---

## Usage

### CLI

```bash
python run_local.py INVOICE [options]

  --provider   google | anthropic | openai | ollama   (default: google)
  --model      override the provider default
  --format     xero | quickbooks | generic            (default: generic)
  --out        write the CSV to a file
```

### HTTP API

```bash
uvicorn app.api:app --reload
```

Interactive docs at http://localhost:8000/docs

```bash
curl -X POST http://localhost:8000/extract \
  -H "X-Api-Key: YOUR_KEY" \
  -F "file=@invoice.pdf" \
  -F "provider=google" \
  -F "output_format=xero"
```

The key travels in the `X-Api-Key` header, per request. With `MULTI_TENANT=true`
the environment fallback is disabled entirely — a missing header returns 401 rather
than quietly using a shared credential.

### Docker

```bash
docker build -t invoice-agent .
docker run -p 8000:8000 -e MULTI_TENANT=true invoice-agent
```

### Running fully local (no API key)

```bash
# install Ollama from ollama.com, then:
ollama pull llama3.2-vision
pip install langchain-ollama

python run_local.py invoice.pdf --provider ollama
```

Slower, and less accurate on messy documents, but free and offline.

---

## Test invoices

`test-invoices/` contains ten generated documents covering the cases that break
extraction agents:

| File | Tests |
|---|---|
| `01_clean_australian` | Baseline — AUD, GST |
| `02_many_line_items` | Summing eight lines |
| `03_us_format` | `06/22/2026` must parse as 22 June, not fail |
| `04_indian_gst` | INR, GSTIN, large comma-separated numbers |
| `05_minimal_receipt` | Missing fields are null, not errors |
| `06_arithmetic_error` | **Deliberately wrong** — must be flagged |
| `07_no_line_items` | Total only, no itemisation |
| `08_messy_with_discount` | EUR, negative discount, currency distractor |
| `09` / `10` scanned | No text layer — forces the vision path |

`EXPECTED_RESULTS.md` lists the correct value for every field.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `limit: 0` on every model | Project has no free-tier allocation | Create a new project at aistudio.google.com; run `check_provider.py` |
| `MODEL NOT AVAILABLE` | Model name not served to your project | Use `gemini-flash-latest` |
| `export: not recognized` | Bash syntax in PowerShell | `$env:VAR = "value"` |
| `Activate.ps1 cannot be loaded` | PowerShell execution policy | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| `credit balance is too low` | Anthropic account has no credits | Add credits, or `--provider google` |
| `Connection refused` on Ollama | Service not running | `ollama serve` |

---

## Response

```json
{
  "success": true,
  "needs_review": false,
  "runtime_seconds": 8.4,
  "source_mode": "text",
  "attempts": 1,
  "invoice": { "vendor_name": "...", "total": "1100.00", "line_items": [...] },
  "issues": [],
  "csv": "ContactName,InvoiceNumber,..."
}
```

`needs_review` is the field that matters operationally. `true` means a human
should look before importing.

---

## Central AI submission details

| Declaration | Value |
|---|---|
| Input surface | File upload (PDF, PNG, JPEG) |
| Required connections | None — no third-party integrations to break |
| Result appearance | Structured data + downloadable CSV |
| Runtime | Docker / Python server |
| `client_config_schema` | `client_config_schema.json` — non-secret fields only |
| Provider | Client-selected: Google, Anthropic, OpenAI, or Ollama |

**Multi-tenancy:** `MULTI_TENANT=true` disables the environment-variable fallback
entirely, so a missing per-request key returns 401 rather than quietly using a
shared credential.

**Listing description must match behaviour exactly** (Developer Terms §5). Do not
claim direct Xero API posting — this produces an import file, it does not write to
an accounting system.

---

## Limitations

- CSV column sets follow Xero and QuickBooks published bill templates; both
  vendors revise these, so verify against the current template before production use.
- Vision path is capped at 3 pages per document.
- Handwritten invoices are not reliably supported.
- No direct write to any accounting system — export only, by design.
