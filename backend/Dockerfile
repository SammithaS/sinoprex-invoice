# Sinoprex Invoice API — multi-arch (linux/arm64 on the Oracle Ampere VM, amd64 elsewhere).
# Build context: the repository root (the app reads ../data at runtime).
#   docker build -f backend/Dockerfile -t invoiceiq-api .

FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/backend/.venv \
    EMBEDDING_CACHE_DIR=/models \
    STORAGE_DIR=/var/invoiceiq/uploads
COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /usr/local/bin/uv
WORKDIR /app/backend

# Dependencies first (cached layer); every package used has arm64 wheels.
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Bake the local embedding model into the image (no download at start-up).
RUN uv run --no-sync python -c "from fastembed import TextEmbedding; \
TextEmbedding('BAAI/bge-small-en-v1.5', cache_dir='/models')"

COPY backend/ ./
COPY data/ /app/data/
ENV PATH=/app/backend/.venv/bin:$PATH

RUN useradd --create-home --uid 10001 app \
    && mkdir -p /var/invoiceiq/uploads /var/invoiceiq/evidence \
    && chown -R app /var/invoiceiq /models
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"
# Migrations are idempotent; then serve behind Caddy (proxy headers trusted from the network).
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'"]
