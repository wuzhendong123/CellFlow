#!/usr/bin/env bash
# CellFlow 本地启动脚本（macOS / Linux + Docker Desktop）
# 用法：./cellflow.sh <命令>，不带参数查看帮助
set -euo pipefail
cd "$(dirname "$0")"

PORT="${CF_PORT:-8000}"
URL="http://localhost:${PORT}"
DC=(docker compose)

c_green() { printf '\033[32m%s\033[0m\n' "$*"; }
c_red() { printf '\033[31m%s\033[0m\n' "$*" >&2; }

need_docker() {
  command -v docker >/dev/null || { c_red "未找到 docker，请先安装并启动 Docker Desktop"; exit 1; }
  docker info >/dev/null 2>&1 || { c_red "Docker 没有运行，请先启动 Docker Desktop"; exit 1; }
  docker compose version >/dev/null 2>&1 || { c_red "需要 docker compose v2（Docker Desktop 自带）"; exit 1; }
}

wait_api() {
  printf '等待服务就绪'
  for _ in $(seq 1 120); do
    if curl -fs "${URL}/healthz" >/dev/null 2>&1; then echo; return 0; fi
    printf '.'; sleep 2
  done
  echo; c_red "服务未在 4 分钟内就绪，查看日志：./cellflow.sh logs"; exit 1
}

usage() {
  cat <<USAGE
CellFlow 本地环境

  ./cellflow.sh up          构建镜像并启动全部服务（MySQL、Redis、API+控制台、Worker），首次约 3~5 分钟
  ./cellflow.sh demo        写入演示数据：业务表、数据源、已发布的方案 hero_config、调用方，并在 ./demo 生成演示 Excel
  ./cellflow.sh submit <文件.xlsx> [VALIDATE_ONLY]
                            像业务服务一样通过 Open API（带签名）提交文件并等待结果（默认 EXECUTE 写表）
  ./cellflow.sh open        在浏览器打开控制台
  ./cellflow.sh status      查看各服务状态
  ./cellflow.sh logs [服务]  跟踪日志（api / worker / mysql / redis）
  ./cellflow.sh mysql       进入 MySQL 命令行（可查看业务库 cellflow_biz 的写入结果）
  ./cellflow.sh test        在容器内运行后端测试
  ./cellflow.sh restart     代码更新后重新构建并重启（git pull 之后用）
  ./cellflow.sh down        停止服务（保留数据）
  ./cellflow.sh reset       停止并清空所有数据（数据库、上传文件）

控制台：${URL}    高危操作口令：${CF_OP_TOKEN:-local-op-token}（可用环境变量 CF_OP_TOKEN 修改）
端口可用环境变量调整：CF_PORT（默认 8000）、CF_MYSQL_PORT（默认 13306）、CF_REDIS_PORT（默认 16379）
USAGE
}

cmd="${1:-help}"; shift || true
case "$cmd" in
  up)
    need_docker
    mkdir -p demo
    "${DC[@]}" up -d --build
    wait_api
    c_green "✓ 已启动：${URL}"
    echo "  高危操作口令：${CF_OP_TOKEN:-local-op-token}"
    echo "  想直接体验：./cellflow.sh demo，然后 ./cellflow.sh submit demo/hero.xlsx"
    ;;
  restart)
    need_docker
    "${DC[@]}" up -d --build
    wait_api
    c_green "✓ 已重启：${URL}"
    ;;
  demo)
    need_docker
    mkdir -p demo
    "${DC[@]}" exec -T api python scripts/demo.py
    c_green "✓ 演示数据就绪。试试："
    echo "  ./cellflow.sh submit demo/hero.xlsx                 # 首次写入业务表"
    echo "  ./cellflow.sh submit demo/hero_changed.xlsx         # 修改血量，查看逐字段变更"
    echo "  ./cellflow.sh submit demo/hero_more.xlsx            # 被安全闸拦截，到控制台「任务 → 待处理」放行"
    echo "  ./cellflow.sh submit demo/hero_newcol.xlsx VALIDATE_ONLY   # 只校验：插入新列导致校验失败"
    echo "  控制台：${URL}（方案 hero_config → 画布 / 发布历史 → 回滚）"
    ;;
  submit)
    need_docker
    f="${1:-}"; mode="${2:-EXECUTE}"
    [ -n "$f" ] && [ -f "$f" ] || { c_red "用法：./cellflow.sh submit <文件.xlsx> [VALIDATE_ONLY]"; exit 1; }
    "${DC[@]}" exec -T api python scripts/openapi_submit.py --name "$(basename "$f")" --mode "$mode" --console "$URL" < "$f"
    ;;
  open)
    (command -v open >/dev/null && open "$URL") || (command -v xdg-open >/dev/null && xdg-open "$URL") || echo "$URL"
    ;;
  status|ps)
    need_docker
    "${DC[@]}" ps
    ;;
  logs)
    need_docker
    "${DC[@]}" logs -f --tail=200 "$@"
    ;;
  mysql)
    need_docker
    "${DC[@]}" exec mysql mysql -ucellflow -pchange_me --default-character-set=utf8mb4 cellflow_biz
    ;;
  test)
    need_docker
    "${DC[@]}" exec -T api python -m pytest -q -p no:warnings "$@"
    ;;
  down)
    need_docker
    "${DC[@]}" down
    ;;
  reset)
    need_docker
    read -r -p "将删除本地 CellFlow 的全部数据（数据库、上传文件），确认？[y/N] " yn
    [ "$yn" = "y" ] || [ "$yn" = "Y" ] || exit 0
    "${DC[@]}" down -v
    rm -rf demo
    c_green "✓ 已清空"
    ;;
  help|-h|--help|*)
    usage
    ;;
esac
