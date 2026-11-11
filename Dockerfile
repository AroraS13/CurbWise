FROM ghcr.io/astral-sh/uv:0.12.21 AS uv
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    PORT=8080

WORKDIR /app

COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --no-cache \
    && useradd --create-home --uid 10001 appuser

COPY app.py context_panel.py context-panel.js index.html ./
COPY nyc ./nyc

RUN chown -R appuser:appuser /app
USER appuser

EXPOSE 8080

CMD ["sh", "-c", "exec uvicorn app:app --host 0.0.0.0 --port ${PORT:-8080}"]
