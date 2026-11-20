# HomeDatabase 文档索引

本目录维护经过代码核验的项目文档。所有技术说明以 `backend/`、`frontend/`、`infra/` 与 [openapi.yaml](../openapi.yaml) 为准。

## `docs/` 文档清单

| 文档 | 状态 | 用途 |
| --- | --- | --- |
| [CONNECT_YOUR_DATA.md](CONNECT_YOUR_DATA.md) | Active | 空库初始化、CSV/TSV/XLSX 导入边界、PostgreSQL 配置、API 接入与排错 |
| [release/PARENTS_GUIDE.md](release/PARENTS_GUIDE.md) | Active | 家庭成员手机 PWA 首次登录、日常记账、分摊与账单导入速查 |
| [AI_ROADMAP.md](AI_ROADMAP.md) | Reference | 已实现能力（含可选 Codex OCR）与未实现 AI 规划的清晰边界及数据流说明 |
| [PUBLIC_DATA.md](PUBLIC_DATA.md) | Reference | 公开脱敏快照包含/排除范围与 `check_public_demo.py` 校验口径 |
| [SUBMISSION.md](SUBMISSION.md) | Historical | 三分钟演示流程与仓库可验证测试基线参考 |
| [CLEANUP_CHANGELOG.md](CLEANUP_CHANGELOG.md) | Reference | 文档去噪、代码核验修正与清理变更记录 |

## 仓库其他技术文档

| 文档 | 状态 | 用途 |
| --- | --- | --- |
| [README.md](../README.md) | Active | 仓库首页、快速体验与本地验证命令 |
| [backend/README.md](../backend/README.md) | Active | 后端架构约束、安全整数/分类迁移 SQL 审计、并发锁顺序、OCR 隔离与备份不变量 |
| [infra/README.md](../infra/README.md) | Active | 国内主节点与海外 worker 双机 Docker Compose 部署、网络边界、OCR profile 与 systemd 定时备份/清理 |
| [examples/README.md](../examples/README.md) | Reference | 虚构 SQLite 演示库、SQLite Schema 与 CSV 导入模板说明 |
| [openapi.yaml](../openapi.yaml) | Active | 后端导出的权威 OpenAPI 契约（39 个 API 路径） |
