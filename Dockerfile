FROM python:3.12-slim

# pdfplumber and pypdfium2 need no system packages beyond these.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
# Declarations the platform reads: connection rule and client setup fields.
COPY central_ai_manifest.json client_config_schema.json ./

# Never run as root in a container that processes untrusted uploads.
RUN useradd -m -u 1000 agent && chown -R agent:agent /app
USER agent

ENV MULTI_TENANT=true \
    LLM_PROVIDER=google \
    PYTHONUNBUFFERED=1

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8000/health')"

CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000"]
