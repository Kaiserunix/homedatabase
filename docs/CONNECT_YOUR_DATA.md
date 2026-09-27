# 接入自己的数据

## 1. 建立自己的空账本

在仓库的 `backend/` 目录执行：

```sh
uv sync --locked --group dev
uv run python scripts/setup_local.py --mode personal --directory data/personal
uv run --env-file data/personal/.env uvicorn homedatabase_api.main:app --app-dir src --host 127.0.0.1 --port 8000
```

脚本会交互设置三个独立密码，创建初始成员、账户、分类，以及空交易表。配置在 `backend/data/personal/.env`，SQLite 在同目录的 `ledger.sqlite3`，加密的上传文件放在 `source-files/`。目录及其密钥不会被 Git 跟踪；密钥丢失后无法恢复已经加密的源文件。

前端启动命令见 [README](../README.md)。`dad`、`mom`、`son` 分别为三个固定身份，`son` 为管理员。个人账户默认只对本人可见，家庭共享账户按授权显示。请用账单归属人的账号导入，不要把所有个人账单都导入管理员名下。

演示和正式数据使用不同目录。切换时先停止当前后端，再以另一个 `--env-file` 启动。已有目录不会被初始化脚本覆盖；升级已有库只运行迁移：

```sh
uv run --env-file data/personal/.env alembic -c alembic.ini upgrade head
```

## 2. CSV / XLSX 导入

使用 [空模板](../examples/import-template.csv)，一行一笔账单：

| 字段 | 示例 | 说明 |
| --- | --- | --- |
| 交易时间 | `2026-09-02 12:00:00` | 推荐完整日期和时间；无时区按上海时间解释 |
| 交易对方 | `示例餐厅` | 商户或摘要，必填 |
| 收支类型 | `支出` / `收入` | 显式填写，避免正负号含义不一致 |
| 金额 | `28.50` | 元；最多两位小数，建议填正数并用收支列表达方向 |
| 币种 | `CNY` | 首次接入建议用人民币，匹配目标账户币种 |
| 交易分类 | `餐饮` | 可选；未匹配到分类时在候选区选择 |

CSV 建议保存为 UTF-8，Excel 可直接保存为 XLSX。系统也识别常见银行、微信等表头别名，以及收入、支出分列，但各平台导出格式不同，应在候选页确认识别结果。

导入路径：手机“我的 → 导入账单”，桌面“导入”。选择自己有管理权限的目标账户，上传文件，在候选区修正日期、金额、方向、分类和外币折算，再确认入账。不要把“文件上传成功”当作“已经入账”。重复记录会提示；不要反复确认同一份文件。

当前表格自动导入仅接受收入和支出。转账、信用卡还款、提现等请使用“记一笔 → 转账”并指定转出与转入账户；退款应关联原支出。不要把它们改成支出来凑合导入。外币交易需要匹配币种账户并确认汇率；家庭基础币固定为 CNY，不能改环境变量来转换已有历史账本。

默认每批最多 10 个文件，单文件 25 MiB，总量 100 MiB，最多 50,000 行。大账单按账户、月份拆分。ZIP、PDF 和图片的实际处理状态以候选页为准；PDF / 图片 OCR 默认关闭，部署启用后再使用。

## 3. 连接自己的 PostgreSQL

数据库连接与导入流水是两件事：连接 PostgreSQL 表示让本程序保存自己的账本，并不表示能直接读取任意银行数据库或其他软件的表结构。

本地连接自己创建的空 PostgreSQL 数据库时，复制 `backend/.env.example` 到 `backend/.env`，修改：

```dotenv
HOMEDATABASE_ENVIRONMENT=local
HOMEDATABASE_DATABASE_URL=postgresql+psycopg://USER:PASSWORD@127.0.0.1:5432/homedatabase
HOMEDATABASE_DEV_MEMBER_ID=
HOMEDATABASE_AUTO_CREATE_SCHEMA=false
HOMEDATABASE_SEED_DEMO=false
HOMEDATABASE_TASK_ALWAYS_EAGER=true
HOMEDATABASE_SOURCE_ENCRYPTION_KEY=<单独生成的32字节URL-safe Base64密钥>
HOMEDATABASE_BACKUP_ENCRYPTION_KEY=<另一把独立密钥>
```

两个密钥分别运行以下命令生成，不能使用占位符：

```sh
uv run python -c "import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip('='))"
```

密码包含 `@`、`:`、`/` 等特殊字符时需要 URL 编码；更简单的做法是生成 URL-safe 随机密码。在 `backend/` 执行：

```sh
uv run --env-file .env alembic -c alembic.ini upgrade head
uv run --env-file .env homedatabase-seed --bootstrap-family
uv run --env-file .env uvicorn homedatabase_api.main:app --app-dir src --host 127.0.0.1 --port 8000
```

公网正式部署按 [infra/README.md](../infra/README.md) 设置 `production`、HTTPS、真实域名、Secure Cookie、Redis 和独立密钥。正式环境禁止 SQLite、开发身份绕过和演示初始化。

其他软件或旧版 Streamlit 的数据库不能直接替换本项目数据库。先导出 CSV/XLSX，再走候选确认导入；不要把旧库直接复制到 `ledger.sqlite3`，也不要对不同表结构的已有库直接执行迁移。

## 4. 从脚本或其他系统接入

本地 API 文档为 `http://127.0.0.1:8000/docs`，静态契约为 [openapi.yaml](../openapi.yaml)。

| 步骤 | API | 用途 |
| --- | --- | --- |
| 登录 | `POST /api/auth/login` | 提交 `username`、`password`，保存服务返回的 Cookie |
| 获取账户 | `GET /api/accounts` | 获取当前成员可见的真实账户 ID |
| 获取分类 | `GET /api/categories` | 获取合法分类 ID |
| 上传账单 | `POST /api/imports` | multipart：`files`、`targetAccountId`，可选 `source` |
| 查看候选 | `GET /api/import-batches/{id}` | 检查解析、重复和分类结果 |
| 确认写入 | `POST /api/import-batches/{id}/confirm` | JSON：`{"candidateIds":["候选ID"]}` |
| 单笔入账 | `POST /api/transactions` | 根据契约提交收支、转账或退款 |

单笔支出示例（先从账户接口取得 `accountId`）：

```json
{
  "type": "expense",
  "payee": "示例餐厅",
  "accountId": "替换为自己的账户ID",
  "amountCents": 2850,
  "currency": "CNY",
  "date": "2026-09-02T12:00:00+08:00"
}
```

`2850` 表示 28.50 元。API 用整数分，CSV 用元。不要把 `28.50` 直接放入 `amountCents`。使用服务的 API 写入可以保留权限校验、复式分录、汇率和账期约束，不建议直接向交易表插 SQL。

## 5. 常见问题

- **页面有账单但数据库没有变化：** 检查前端环境变量，`VITE_USE_MOCKS` 必须为 `false` 或不设置；变更后重启 Vite，部署产物需重新构建。
- **401 / 无法登录：** 使用初始化时设置的密码，确认启动命令加载了对应目录的 `.env`。
- **上传提示缺少源文件密钥：** 使用初始化脚本生成的配置，或设置自己的 `HOMEDATABASE_SOURCE_ENCRYPTION_KEY` 并重启后端。
- **PDF 一直等待 OCR：** 默认没有启用 OCR，可先导出 CSV/XLSX，或按部署文档配置可选 OCR 工作器。
- **看不到某人的个人账户：** 属于成员隔离行为，使用账户所有人的身份登录，或明确授予共享权限。
- **备份：** PostgreSQL 正式部署使用项目的加密备份流程；本地 SQLite 请先停止后端，再备份整个个人数据目录，并妥善保管配置中的密钥。
