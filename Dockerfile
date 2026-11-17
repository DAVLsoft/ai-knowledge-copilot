# syntax=docker/dockerfile:1

# ---------- builder: resolve & install deps with uv ----------
FROM python:3.13-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.13 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Dependencies layer: rebuilt only when pyproject.toml / uv.lock change.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev

COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

# ---------- runtime: no uv, no build tools, non-root ----------
FROM python:3.13-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

RUN groupadd --system --gid 1000 app \
 && useradd --system --uid 1000 --gid app --no-create-home app

WORKDIR /app
COPY --from=builder --chown=app:app /app /app

USER app
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=2).status != 200)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
