FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md alembic.ini ./
COPY leadcapture ./leadcapture
RUN pip install ".[postgres,redis]"

RUN useradd --system --create-home --uid 10001 app && mkdir -p /data && chown app /data
USER app

ENV DATABASE_URL=sqlite+aiosqlite:////data/leadcapture.db \
    LOG_FORMAT=json

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).status == 200 else 1)"

CMD ["sh", "-c", "leadcapture migrate && uvicorn leadcapture.main:create_app --factory --host 0.0.0.0 --port 8000 --proxy-headers"]
