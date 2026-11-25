# Stage 1: Build React SPA
FROM node:22-alpine AS frontend-builder
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: Python Runtime + FastAPI + Embedded SQLite/DuckDB/Parquet
FROM python:3.12-slim
WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai \
    CHANCE_DATA_DIR=/app/data \
    CHANCE_FRONTEND_DIST_DIR=/app/frontend/dist \
    CHANCE_HOST=0.0.0.0 \
    CHANCE_PORT=8000

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml README.md ./
COPY backend/ ./backend/
RUN uv sync --no-dev --no-editable

COPY --from=frontend-builder /web/dist ./frontend/dist

VOLUME ["/app/data"]
EXPOSE 8000

CMD ["uv", "run", "--no-sync", "chancedetect", "serve", "--host", "0.0.0.0", "--port", "8000"]
