# Security Policy · 安全策略

## Reporting a vulnerability · 报告漏洞

Please **do not** open a public issue for security problems.
Use GitHub's private reporting: **Security → Report a vulnerability** on this repository.
Include the affected version or commit, reproduction steps, and the impact you observed.

请不要用公开 Issue 报告安全问题。请在本仓库的 **Security → Report a vulnerability** 中私密提交，并附上受影响的版本或 commit、复现步骤和影响范围。

## Scope · 范围

In scope: the Open API (signature auth, idempotency), the console API, credential storage for data sources, and the write path to business tables.

范围包括：Open API（签名鉴权、幂等）、控制台接口、数据源凭据的存储，以及写入业务表的链路。

## Notes for deployers · 部署提示

- The defaults in `.env.example`, `docker-compose.yml` and `cellflow.sh` (database passwords, `CF_OP_TOKEN`, demo secrets) are local placeholders. Replace all of them in any shared or production environment.
- v1 has no built-in login: run the console on an internal network and protect it with the operation token.

- `.env.example`、`docker-compose.yml`、`cellflow.sh` 里的默认值（数据库口令、`CF_OP_TOKEN`、演示密钥）都只是本地占位值，任何共享或生产环境都必须替换。
- v1 没有内置登录：请把控制台部署在内网，并使用操作口令保护高危操作。
