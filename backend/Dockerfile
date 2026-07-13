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

# curl is only used by the container HEALTHCHECK below.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 appuser

COPY --from=builder /opt/venv /opt/venv
COPY . .

# Build the local RAG vector store at image-build time (pure local
# sentence-transformers embedding, no API key needed) so containers start
# fast and don't re-embed the knowledge base on every boot.
RUN python -m app.rag.ingest

RUN chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
