# syntax=docker/dockerfile:1.7

ARG PYTHON_VERSION=3.13
ARG UV_VERSION=0.11.7

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv-bin

FROM python:${PYTHON_VERSION}-slim-trixie AS builder
COPY --from=uv-bin /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /app/backend

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
COPY migrations ./migrations
COPY alembic.ini README.md ./
RUN uv sync --frozen --no-dev

FROM python:${PYTHON_VERSION}-slim-trixie AS runtime

ENV PATH="/app/backend/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# pg_dump must be at least as new as the PostgreSQL server. Debian trixie ships
# PostgreSQL 17 client tools, matching compose.domestic.yml.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates postgresql-client tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 homedatabase \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin homedatabase

WORKDIR /app/backend
COPY --from=builder --chown=10001:10001 /app/backend/.venv ./.venv
COPY --from=builder --chown=10001:10001 /app/backend/src ./src
COPY --from=builder --chown=10001:10001 /app/backend/migrations ./migrations
COPY --from=builder --chown=10001:10001 /app/backend/alembic.ini ./alembic.ini

RUN mkdir -p /var/lib/homedatabase/source-files /var/lib/homedatabase/backups \
    && chown -R 10001:10001 /var/lib/homedatabase

USER 10001:10001
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; host=os.environ.get('HOMEDATABASE_WEBAUTHN_RP_ID', 'localhost'); request=urllib.request.Request('http://127.0.0.1:8000/api/health', headers={'Host': host}); urllib.request.urlopen(request, timeout=4).read()"]

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "homedatabase_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "127.0.0.1"]

FROM node:22-bookworm-slim AS ocr-runner

ARG CODEX_CLI_VERSION=0.145.0

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates python3 tini \
    && rm -rf /var/lib/apt/lists/* \
    && npm install --global "@openai/codex@${CODEX_CLI_VERSION}" \
    && groupadd --gid 10001 homedatabase \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin homedatabase \
    && mkdir -p /opt/homedatabase_api /var/lib/homedatabase/ocr-jobs /home/homedatabase/.codex \
    && chown -R 10001:10001 /var/lib/homedatabase /home/homedatabase

COPY --chown=10001:10001 src/homedatabase_api/__init__.py /opt/homedatabase_api/__init__.py
COPY --chown=10001:10001 src/homedatabase_api/ocr_contract.py /opt/homedatabase_api/ocr_contract.py
COPY --chown=10001:10001 src/homedatabase_api/ocr_runner.py /opt/homedatabase_api/ocr_runner.py

ENV PYTHONPATH=/opt \
    HOME=/home/homedatabase \
    CODEX_HOME=/home/homedatabase/.codex \
    HOMEDATABASE_OCR_JOBS_DIR=/var/lib/homedatabase/ocr-jobs \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER 10001:10001

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python3", "-m", "homedatabase_api.ocr_runner"]

# Keep the ordinary backend runtime as the default Docker build result.  The
# isolated Codex image is selected explicitly with --target ocr-runner.
FROM runtime AS default
