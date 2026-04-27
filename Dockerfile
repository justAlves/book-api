# syntax=docker/dockerfile:1.7
FROM python:3.14-slim-bookworm

# uv (fast Python package manager) — copied as a static binary
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PATH=/app/.venv/bin:$PATH

WORKDIR /app

# 1) Resolve Python deps first so this layer caches independently of source.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# 2) Install Chromium + every system lib Playwright needs (apt under the hood).
RUN playwright install --with-deps chromium && \
    rm -rf /var/lib/apt/lists/*

# 3) App source.
COPY . .

EXPOSE 8000

CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
