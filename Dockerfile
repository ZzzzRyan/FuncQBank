# FuncQBank — single self-contained image (FastAPI + SQLite, managed by uv)
FROM python:3.13-slim

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

# uv for dependency management
RUN pip install --no-cache-dir uv

# Install deps first (cached layer)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen

# App code + question content (docs images + extracted JSON)
COPY . .

EXPOSE 8000
# Seed the DB on first boot (volume empty), then serve.
CMD ["sh", "-c", "test -f \"${DB_PATH:-/app/data/app.db}\" || uv run --no-sync scripts/seed.py; uv run --no-sync uvicorn app.main:app --host 0.0.0.0 --port 8000"]
