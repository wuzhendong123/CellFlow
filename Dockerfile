# CellFlow 本地一体化镜像：前端构建产物由 FastAPI 托管；API 与 Worker 共用此镜像
# ---------- 前端构建 ----------
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------- 后端 ----------
FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app/backend
COPY backend/pyproject.toml ./
# 先只装依赖（利用构建缓存），再拷源码
RUN mkdir cellflow && touch cellflow/__init__.py && pip install -e ".[dev]" cryptography && rm -rf cellflow
COPY backend/ ./
RUN pip install --no-deps -e .
COPY --from=web /web/dist /app/frontend/dist
ENV CF_CONSOLE_DIR=/app/frontend/dist
EXPOSE 8000
CMD ["uvicorn", "cellflow.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
