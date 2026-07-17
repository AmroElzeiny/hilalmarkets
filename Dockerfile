FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=10 \
    PIP_INDEX_URL=https://pypi.org/simple \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY scripts ./scripts
COPY alembic.ini ./
COPY alembic ./alembic
RUN python -m pip install --no-cache-dir --upgrade pip setuptools wheel \
    && python -m pip install --no-cache-dir --prefer-binary .

FROM base AS test

RUN apt-get update \
    && apt-get install --no-install-recommends --yes git nodejs \
    && rm -rf /var/lib/apt/lists/*
RUN python -m pip install --no-cache-dir --prefer-binary ".[dev]"

FROM base AS runtime

CMD ["uvicorn", "ai_market_monitor.main:app", "--host", "0.0.0.0", "--port", "8000"]
