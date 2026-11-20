# 接入自己的数据

> 状态：`Active` · 覆盖本地空库初始化、账单导入、PostgreSQL 连接与脚本 API 接入。

## 1. 建立本地空账本

在仓库 `backend/` 目录执行：

```sh
uv sync --locked --group dev
uv run python scripts/setup_local.py --mode personal --directory data/personal
uv run --env-file data/personal/.env uvicorn homedatabase_api.main:app --app-dir src --host 127.0.0.1 --port 8000
```

脚本交互设置 `dad`、`mom`、`son` 三个独立密码（至少 12 位，互不相同；`son` 为管理员），初始化成员、账户、分类和空交易表，并在 `backend/data/personal/` 生成 `.env`、`ledger.sqlite3`、`source-files/`、`backups/`、`ocr-jobs/` 与两把本地加密密钥。`**/data/` 已被 `.gitignore` 忽略；密钥丢失后已加密源文件与备份无法恢复。

前端启动见 [README](../README.md)。首次打开登录页时，点击 **密码登录** 展开密码框（Passkey 需先登录后在安全页绑定）。个人账户默认仅本人可见，请用账单归属人账号导入，不要全导入管理员名下。

演示库与正式库使用不同目录，切换时先停后端再换 `--env-file` 启动。初始化脚本拒绝覆盖已有目录；升级已有库只执行迁移：

```sh
uv run --env-file data/personal/.env alembic -c alembic.ini upgrade head
```

## 2. CSV / TSV / XLSX 导入

使用 [空模板](../examples/import-template.csv)，一行一笔账单（UTF-8 CSV/TSV 或 XLSX）：

| 字段 | 示例 | 说明 |
| --- | --- | --- |
| 交易时间 | `2026-09-02 12:00:00` | 推荐完整日期时间；无时区按 `Asia/Shanghai` 解释 |
| 交易对方 | `示例餐厅` | 商户或摘要，必填 |
| 收支类型 | `支出` / `收入` | 显式填写，避免正负号歧义 |
| 金额 | `28.50` | 主币单位（元），最多两位小数，建议正数 |
| 币种 | `CNY` | 三位大写币种码，默认人民币 |
| 交易分类 | `餐饮` | 可选；未匹配时可在候选区调整 |

系统支持常见银行/微信表头别名与收入/支出分列，但各平台导出差异较大，必须在候选页核对后再确认。

- **入口**：手机 **我的 → 导入账单**；桌面管理台 **导入与确认**（仅管理员可进管理台）。
- **候选复核边界**：上传成功不等于入账。Web 候选页支持行内调整分类、为未指定账户的候选补选目标账户、为外币候选补填汇率/人民币折算金额，以及跳过重复或确认入账（日期、商户、金额、方向在界面中只读展示）；API `PATCH /api/import-batches/{batch_id}/candidates/{candidate_id}` 可按 `expectedVersion` 修改 `date`、`payee`、`amountCents`、`fxRate`、`baseAmountCents`、`suggestedCategoryId`、`targetAccountId`，但**不支持修改 `direction`（收支方向）**——若方向解析错误，请跳过该候选并在原表格修正或手工录入。
- **交易类型与外币**：表格导入仅生成支出/收入。转账、还款、提现请用 **记一笔 → 转账**；退款应关联原支出。外币交易可入同币种账户或 `CNY` 基础币账户（按人民币实扣入账），均需提供 `fxRate` 或 `baseAmountCents`（两者同时给出时误差不得超过 1 分）。家庭基础币固定为 `CNY`，不可通过改环境变量转换历史账本。
- **格式与配额**：默认每批最多 `10` 个文件、单文件 `25 MiB`、完整 multipart 请求体 `100 MiB`、最多 `50,000` 行。直接解析 `.csv` / `.tsv` / `.xlsx`（XLSX 解压上限 `100 MiB`）；旧版 `.xls` 与 `.zip` 会被拒绝；`.pdf` / `.png` / `.jpg` / `.jpeg` / `.webp` / `.heic` 经文件头校验后进入 `needs_ocr`（仅在启用 OCR 工作器时自动识别）。

## 3. 连接自己的 PostgreSQL

连接 PostgreSQL 仅决定本程序把账本存到哪里，不能直接读取其他软件的库结构。本地连接空 PostgreSQL 库时，复制 `backend/.env.example` 为 `backend/.env` 并配置：

```dotenv
HOMEDATABASE_ENVIRONMENT=local
HOMEDATABASE_DATABASE_URL=postgresql+psycopg://USER:PASSWORD@127.0.0.1:5432/homedatabase
HOMEDATABASE_DEV_MEMBER_ID=
HOMEDATABASE_AUTO_CREATE_SCHEMA=false
HOMEDATABASE_SEED_DEMO=false
HOMEDATABASE_TASK_ALWAYS_EAGER=true
HOMEDATABASE_SOURCE_ENCRYPTION_KEY=<独立32字节URL-safe Base64密钥>
HOMEDATABASE_BACKUP_ENCRYPTION_KEY=<另一把独立32字节密钥>
```

分别生成两把密钥（无 `=` 填充亦可）：

```sh
uv run python -c "import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip('='))"
```

数据库密码含特殊字符时需 URL 编码（建议直接用 URL-safe 随机密码）。在 `backend/` 执行：

```sh
uv run --env-file .env alembic -c alembic.ini upgrade head
uv run --env-file .env homedatabase-seed --bootstrap-family
uv run --env-file .env uvicorn homedatabase_api.main:app --app-dir src --host 127.0.0.1 --port 8000
```

公网正式部署按 [infra/README.md](../infra/README.md) 配置 `production`、HTTPS、Secure Cookie、Redis 与独立密钥。旧库或其他软件数据库请先导出 CSV/XLSX 走候选导入，勿直接覆盖 `ledger.sqlite3` 或直接执行迁移。

## 4. 脚本与 API 接入

本地 Swagger UI 为 `http://127.0.0.1:8000/api/docs`，OpenAPI JSON 为 `http://127.0.0.1:8000/api/openapi.json`，静态契约为 [openapi.yaml](../openapi.yaml)。

| 步骤 | API | 用途 |
| --- | --- | --- |
| 登录 | `POST /api/auth/login` | 提交 `username`、`password`，保存 `hd_session` Cookie |
| 获取账户 | `GET /api/accounts` | 获取当前成员可见的账户 ID 与权限 |
| 获取分类 | `GET /api/categories` | 获取启用分类 ID（管理员可加 `?includeInactive=true`） |
| 上传账单 | `POST /api/imports` | multipart：`files`、`targetAccountId`，可选 `source` |
| 查看候选 | `GET /api/import-batches/{batch_id}` | 检查解析、重复与校验状态 |
| 修改候选 | `PATCH /api/import-batches/{batch_id}/candidates/{candidate_id}` | 按 `expectedVersion` 修改字段（不含 `direction`） |
| 确认写入 | `POST /api/import-batches/{batch_id}/confirm` | JSON：`{"candidateIds":["候选ID"]}` |
| 单笔入账 | `POST /api/transactions` | 提交支出、收入、转账或退款 |

单笔支出示例（`amountCents` 单位为**分**，`2850` = 28.50 元；请经 API 写入以保留权限、复式分录、汇率与账期校验）：

```json
{
  "type": "expense",
  "payee": "示例餐厅",
  "accountId": "替换为真实账户ID",
  "amountCents": 2850,
  "currency": "CNY",
  "date": "2026-09-02T12:00:00+08:00"
}
```

## 5. 常见问题

- **页面有数据但数据库无变化**：确认前端 `VITE_USE_MOCKS` 未设为 `true`；修改后重启 Vite 或重新构建。
- **401 / 登录失败**：确认启动后端加载了对应目录的 `.env`，且首次登录先点击 **密码登录**。
- **加密密钥报错**：`production` 启动强制要求 `HOMEDATABASE_SOURCE_ENCRYPTION_KEY` 与 `HOMEDATABASE_BACKUP_ENCRYPTION_KEY`；`local` 未设源文件密钥会自动在数据目录生成 `.source-encryption-key`（若配置了非法格式密钥会报错），而执行备份即使在本地也必须配置 `HOMEDATABASE_BACKUP_ENCRYPTION_KEY`。
- **PDF / 图片停在 `needs_ocr`**：默认未开启 OCR；可导出 CSV/XLSX 导入，或按 [infra/README.md](../infra/README.md) 启用可选 OCR profile。
- **看不到他人个人账户**：属默认权限隔离；请用归属人登录或由归属人授予共享权限。
- **本地备份**：PostgreSQL 生产环境使用加密备份流程；本地 SQLite 请先停后端，再备份整个数据目录并妥善保管 `.env` 中的密钥。

[文档索引](INDEX.md) · [日常使用指南](release/PARENTS_GUIDE.md) · [公开数据范围](PUBLIC_DATA.md)
