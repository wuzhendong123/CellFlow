#!/usr/bin/env bash
# 端到端验收（T29）：准备独立的 e2e 库 → 构建前端 → 启动 API（托管 dist）与 arq Worker → 跑 Playwright。
# 依赖本地 MySQL 8 / Redis；连接串用环境变量覆盖，这里的默认值只适用于本地开发库。
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PY:-python3}
export CF_E2E_MYSQL_URL=${CF_E2E_MYSQL_URL:-${CF_TEST_MYSQL_URL:-mysql+pymysql://cellflow:cellflow_dev@127.0.0.1:3306}}
export CF_E2E_META_DB=cellflow_e2e_meta CF_E2E_BIZ_DB=cellflow_e2e_biz
export CF_E2E_DATA=${CF_E2E_DATA:-$PWD/e2e/.data}
export CF_META_DB_URL="$CF_E2E_MYSQL_URL/$CF_E2E_META_DB?charset=utf8mb4"
export CF_REDIS_URL=${CF_E2E_REDIS_URL:-redis://127.0.0.1:6379/14}
export CF_STORAGE_BACKEND=local CF_STORAGE_LOCAL_DIR="$CF_E2E_DATA/storage"
export CF_OP_TOKEN=e2e-op-token CF_SECRET_KEY=e2e-secret-key
export CF_REF_E2E_BIZ="$CF_E2E_MYSQL_URL" CF_REF_E2E_APP_SECRET=e2e-app-secret
export CF_E2E_BASE=${CF_E2E_BASE:-http://127.0.0.1:8765}
unset CF_JOB_INLINE

rm -rf "$CF_E2E_DATA"
redis-cli -u "$CF_REDIS_URL" flushdb >/dev/null
$PY e2e/prepare.py "$CF_E2E_DATA"
[ "${SKIP_BUILD:-0}" = 1 ] || npm run build >/dev/null

(cd ../backend && exec $PY -m uvicorn cellflow.api.main:app --port "${CF_E2E_BASE##*:}" --log-level warning) &
API=$!
(cd ../backend && exec $PY -m arq cellflow.runtime.worker.WorkerSettings) >"$CF_E2E_DATA/worker.log" 2>&1 &
WORKER=$!
trap 'kill $API $WORKER 2>/dev/null || true' EXIT
for _ in $(seq 60); do curl -sf "$CF_E2E_BASE/healthz" >/dev/null && break; sleep 0.5; done
npx playwright test "$@"
