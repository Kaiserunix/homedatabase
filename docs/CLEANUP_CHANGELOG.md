# 文档清理与代码核验记录

> 记录 `swarm/doc-cleanup` 分支对 `Kaiserunix/homedatabase` 文档树的去噪与代码一致性审计结果。本次清理仅修改 `docs/**`，未改动任何应用源码、测试、CI、`openapi.yaml` 或根目录 `README.md`。

## 1. 精简与重构清单

| 文件 | 操作 | 行数变化 | 原因与保留内容 |
| --- | --- | --- | --- |
| [AI_ROADMAP.md](AI_ROADMAP.md) | 去噪重构 | 107 → 30 | 删除 5 段虚构对话示例、营销口号与重复架构表；保留经代码核验的“已实现 vs 规划中”对照、5 阶段技术前提/验证门槛与 OCR 数据边界 |
| [SUBMISSION.md](SUBMISSION.md) | 去噪归档（Historical） | 90 → 26 | 删除备选标题、与 README 重复的营销段落/亮点表/问答；保留文件路径以维持 `README.md` 链接有效，仅留存客观三分钟演示流程与已核验的测试基线（101 后端 / 189 前端） |
| [CONNECT_YOUR_DATA.md](CONNECT_YOUR_DATA.md) | 纠错精简 | 115 → 113 | 保留全部有效命令、CSV 模板、PostgreSQL 配置、API 接入表与 JSON 示例；修正 7 处与源码不一致的描述（见下节） |
| [PUBLIC_DATA.md](PUBLIC_DATA.md) | 代码核验微调 | 23 → 24 | 补齐 `check_public_demo.py` 实际校验项（`0004_category_name_uniqueness`、无 `scrypt$` 残留、12 张空运行表）与文档导航链接 |
| [release/PARENTS_GUIDE.md](release/PARENTS_GUIDE.md) | 新增修复死链 | 0 → 24 | 补齐 `README.md:27` 原有的缺失目标 `docs/release/PARENTS_GUIDE.md`（原 `.gitignore:38` 的 `release/` 误匹配导致该路径未入库，本次显式跟踪），提供严格对应手机端代码的日常使用说明 |
| [INDEX.md](INDEX.md) | 新增 | 0 → 24 | 建立全仓文档索引，标注每份文档的一行用途与状态（Active / Reference / Historical） |
| [CLEANUP_CHANGELOG.md](CLEANUP_CHANGELOG.md) | 新增 | 0 → 32 | 记录精简项、代码核验修正及越界文件只读审计发现 |

## 2. 依据源码修正的真实偏差

1. **本地 Swagger UI 地址错误**（`CONNECT_YOUR_DATA.md`）：原文档写 `http://127.0.0.1:8000/docs`（实际 404）；`backend/src/homedatabase_api/main.py:39-40` 挂载为 `/api/docs` 与 `/api/openapi.json`。
2. **候选复核可编辑字段边界**（`CONNECT_YOUR_DATA.md`）：原文档称可在候选区修正“日期、金额、方向、分类和外币折算”；`schemas.py:282-292` 与 `openapi.yaml` 均不支持修改 `direction`，而 Web UI（`ImportDetail.tsx:449-490`）仅支持行内调整分类、未指定的目标账户与外币汇率/折算金额。
3. **桌面管理台导航名称**（`CONNECT_YOUR_DATA.md`）：原文档写桌面“导入”；`frontend/src/admin/App.tsx:36` 实际标签为 **导入与确认**。
4. **导入格式与 ZIP 行为**（`CONNECT_YOUR_DATA.md`）：原文档模糊称“ZIP、PDF 和图片状态以候选页为准”；`services/import_parsers.py:195-210` 直接解析 `.csv`/`.tsv`/`.xlsx`，拒绝 `.xls` 和 `.zip`，仅将 `.pdf`/`.png`/`.jpg`/`.jpeg`/`.webp`/`.heic` 送入 `needs_ocr`。
5. **外币入账账户口径**（`CONNECT_YOUR_DATA.md`）：`services/ledger.py:374-382` 支持外币交易入同币种账户或 `CNY` 基础币账户（按 `baseAmountCents` 入账）。
6. **本地与生产密钥行为**（`CONNECT_YOUR_DATA.md`）：`services/source_storage.py:152-167` 在 `local` 缺省时自动生成 `.source-encryption-key`，仅格式非法或 `production` 缺省时报错；`services/backups.py:70-79` 执行备份即使本地也要求配置 `HOMEDATABASE_BACKUP_ENCRYPTION_KEY`。
7. **首次登录交互**（`CONNECT_YOUR_DATA.md`、`release/PARENTS_GUIDE.md`）：`frontend/src/components/LoginPage.tsx:30` 默认折叠密码框并主推 Passkey，首次密码登录需先点击 **密码登录** 展开。

## 3. 范围外文档的只读审计记录（未修改）

- `backend/README.md`、`examples/README.md`：经逐项核对，命令、迁移 SQL、并发锁、OCR 隔离与示例库描述均与当前代码一致。
- `infra/README.md`：核心部署与资源限额均与 `infra/compose.*.yml`、`infra/systemd/*` 一致；仅第 30 行写“分别生成三个不同值”，实际紧随 4 条命令（PostgreSQL 密码、Redis 密码、源文件密钥、备份密钥），留待 `infra/` 维护者顺手更正。
- `.gitignore:38`：`release/` 未加前导 `/` 会同时忽略 `docs/release/`，建议在根目录 `.gitignore` 维护流中按需收窄为 `/release/`。
- `README.md`：由策略/文案流独立维护；其第 27 行原有的 `docs/release/PARENTS_GUIDE.md` 死链已通过在 `docs/` 内补齐目标文档解决。
