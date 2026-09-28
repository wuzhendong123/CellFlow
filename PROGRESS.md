# CellFlow 开发进度（PROGRESS.md）

> 验收方式（2026-09-28 确认）：**整体验收**——各任务连续推进，全部完成后统一验收；每个任务完成时在此记录「做了什么、怎么验证的、风险点」。

## 偏差与待验收事项清单

开发中与定稿文档不一致、或需要你在整体验收时确认的事项，统一登记在这里：

| # | 任务 | 事项 | 原因 | 影响 |
|---|---|---|---|---|
| V1 | T01 | 测试不使用 testcontainers，改为读取环境变量提供的 MySQL / Redis（`CF_TEST_MYSQL_URL`、`CF_TEST_REDIS_URL`），每次测试会话自动建删临时库；本地开发另提供 `deploy/docker-compose.yml` | 开发环境没有 Docker 守护进程；CI 用服务容器提供 MySQL / Redis | 少一个开发依赖；测试方式等价 |
| V2 | T01 | 新增运行时依赖 `python-multipart`（FastAPI 处理文件上传的必需组件）、开发依赖 `httpx`（FastAPI 测试客户端必需）；CI 额外安装 `cryptography`（PyMySQL 连接 MySQL 8 默认认证方式所需） | 已批准依赖的必需配套 | 无功能影响 |
| V3 | T01 | 元数据表相对 §3 的小幅增补：`cf_parse_job.result`（任务结果摘要，含预判）、`cf_outbox.last_error`（最近一次投递失败原因）；`cf_snapshot.job_id` 允许为空（接管基线快照没有任务） | 实现回调记录、预判展示、基线所需 | 仅新增可空列，不影响已定义接口 |

## 任务记录
