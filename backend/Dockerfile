# syntax=docker/dockerfile:1

# --- Stage 1: build a venv with all Python deps -----------------------------
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# tokenizers/sentence-transformers/chromadb mostly ship prebuilt wheels, but
# build-essential covers the rare sdist-only fallback on a given platform.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .

# sentence-transformers (RAG embeddings) pulls in torch, which on PyPI
# defaults to the CUDA build (~4-5 GB of unused nvidia-*/triton packages) —
# a droplet has no GPU, so install the CPU-only wheel first; pip then finds
# torch already satisfied and skips the CUDA variant when it hits
# requirements.txt.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir -r requirements.txt

# --- Stage 2: runtime image ---------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# curl is only used by the container HEALTHCHECK below. The libpango/libcairo/
# libgdk-pixbuf/libffi set is WeasyPrint's native dependency for HTML->PDF
# rendering (boarding-pass delivery PDFs) — unlike macOS/Homebrew, apt installs
# these to the standard linker search path, so no DYLD_* shim is needed here.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        libpango-1.0-0 \
        libpangocairo-1.0-0 \
        libpangoft2-1.0-0 \
        libgdk-pixbuf-2.0-0 \
        libcairo2 \
        libffi8 \
        shared-mime-info \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 appuser

COPY --from=builder /opt/venv /opt/venv
COPY . .

# Build the local RAG vector store at image-build time (pure local
# sentence-transformers embedding, no API key needed) so containers start
# fast and don't re-embed the knowledge base on every boot.
RUN python -m app.rag.ingest

RUN chmod +x /app/entrypoint.sh && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# The entrypoint bootstraps the DB schema (API container only; the worker sets
# ALEMBIC_SKIP=1) then execs the CMD. API default below; the worker overrides
# CMD with `arq app.queue.worker.WorkerSettings`.
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
