# Multi-stage build: dependencies are resolved once, the runtime image stays small
# and runs as a non-root user.

FROM python:3.11-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY app ./app
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install ".[monitoring]"

# ---------------------------------------------------------------- runtime

FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

# libpq for asyncpg's runtime needs; no compiler in the final image.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 handyai

COPY --from=builder /opt/venv /opt/venv

WORKDIR /srv
COPY --chown=handyai:handyai app ./app
# alembic.ini and alembic/ are added with the first migration (next Phase 2 commit).

USER handyai
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8000/health/live || exit 1

# Workers are set by the orchestrator; one process per container keeps scaling
# and log correlation simple.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
