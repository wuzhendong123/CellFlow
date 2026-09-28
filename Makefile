# 常用命令（需先 cp .env.example .env 并按需修改）
PY ?= python3

.PHONY: deps install migrate api worker test lint e2e perf

deps:            ## 启动本地依赖（MySQL / Redis / MinIO）
	docker compose -f deploy/docker-compose.yml up -d

install:         ## 安装后端（含开发依赖）
	cd backend && $(PY) -m pip install -e ".[dev]"

migrate:         ## 执行元数据库迁移
	cd backend && set -a && . ../.env && set +a && alembic upgrade head

api:             ## 启动 API 服务
	cd backend && set -a && . ../.env && set +a && uvicorn cellflow.api.main:app --reload --port 8000

worker:          ## 启动任务 Worker
	cd backend && set -a && . ../.env && set +a && arq cellflow.runtime.worker.WorkerSettings

test:            ## 后端测试（需要可用的 MySQL / Redis，见 backend/tests/conftest.py）
	cd backend && $(PY) -m pytest

lint:
	cd backend && ruff check cellflow tests alembic

e2e:             ## 端到端验收（T29）：独立 e2e 库 + 真实 Worker + Playwright（需要 MySQL / Redis，前端已 npm ci）
	cd frontend && PY=$(PY) ./e2e/run.sh

perf:            ## 性能验收（10 万行 × 5 列文件，约 3 分钟）
	cd backend && CF_PERF=1 $(PY) -m pytest -s tests/test_perf.py
