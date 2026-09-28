# CellFlow

不规则 Excel 的可视化解析配置与自动写表平台：在 Web 控制台里为一类 Excel 配置一次解析方案（圈选区域 → 编排清洗/关联/校验 → 绑定业务表 → 发布），之后业务服务通过 Open API 提交文件，CellFlow 自动解析、校验并写入 MySQL 业务表，支持安全闸、人工放行与秒级回滚。

设计文档：[PRD](PRD.md) · [技术设计](TECH_DESIGN.md) · [交互原型](WIREFRAME.md) · [任务清单](TODO.md) · [开发进度](PROGRESS.md)

## 本地启动（macOS / Linux + Docker Desktop）

```bash
git clone https://github.com/wuzhendong123/CellFlow.git && cd CellFlow
./cellflow.sh up          # 构建并启动 MySQL、Redis、API + 控制台、Worker（首次约 3~5 分钟）
./cellflow.sh demo        # 可选：写入演示方案与演示 Excel（./demo 目录）
./cellflow.sh submit demo/hero.xlsx   # 像业务服务一样带签名提交文件并等待结果
./cellflow.sh open        # 浏览器打开 http://localhost:8000
```

- 高危操作（发布、放行、回滚、改设置等）的口令默认是 `local-op-token`，可用环境变量 `CF_OP_TOKEN` 修改。
- 数据源可以配置多个：控制台「数据源 → 新建」选「直接填写」，填主机、端口、账号、口令、库名即可（口令加密保存，不回显）。连接本机 Docker 里的 MySQL 主机填 `mysql`、端口 `3306`；连接 Mac 本机上的 MySQL 主机填 `host.docker.internal`。也可以选「环境变量引用」，例如引用名 `BIZ_MYSQL` + 库名 `cellflow_biz`（本地业务库，`./cellflow.sh mysql` 可进入查看）。新建调用方时签名密钥引用名填 `DEMO_APP_SECRET`。
- 其他命令：`status`、`logs [api|worker]`、`test`（容器内跑后端测试）、`restart`（`git pull` 后重建）、`down`、`reset`（清空数据）。`./cellflow.sh` 不带参数查看帮助。
- 端口冲突时：`CF_PORT=8080 CF_MYSQL_PORT=23306 ./cellflow.sh up`。

所有口令均为本地占位值，只用于本机。

## 开发

后端 `backend/`（Python 3.11、FastAPI、SQLAlchemy、arq），前端 `frontend/`（React 18、antd、React Flow、Univer）。常用命令见 `Makefile`：`make test`、`make lint`、`make e2e`、`make perf`。
