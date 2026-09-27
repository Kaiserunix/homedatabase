# HomeDatabase backend

这里是 HomeDatabase 的权威后端，配合仓库内 `frontend/` 使用。首次启动与接入自己的账单，请先阅读根目录 README 和 `docs/CONNECT_YOUR_DATA.md`。

开发环境使用 SQLite 运行快速测试；生产环境只允许 PostgreSQL。API 默认挂载在 `/api`，金额对外使用整数 `*Cents` 字段，数据库内部同样不使用浮点数。首版家庭基础币固定为 CNY；不能靠修改环境变量把已有账本切换成另一基础币，未来如需切换必须做显式数据迁移。

```powershell
Set-Location backend
Copy-Item .env.example .env
uv sync --group dev
uv run --env-file .env alembic -c alembic.ini upgrade head
uv run --env-file .env homedatabase-seed --demo --password "请替换为本地强密码"
uv run --env-file .env uvicorn homedatabase_api.main:app --app-dir src --reload
uv run --env-file .env pytest
```

生产环境在迁移完成后，只初始化一次固定家庭成员；命令会在终端分别读取三个不同密码，不把密码放进命令行历史：

```powershell
uv run --env-file .env homedatabase-seed --bootstrap-family
```

接口契约由后端生成，前端只消费生成物：

```powershell
Set-Location backend
$env:HOMEDATABASE_ENVIRONMENT = "test"
$env:HOMEDATABASE_DATABASE_URL = "sqlite+pysqlite:///:memory:"
uv run python scripts/export_openapi.py
Set-Location ..
npm --prefix frontend run api:gen
```

分类列表默认只返回启用项；分类管理页由家庭管理员显式请求
`GET /api/categories?includeInactive=true`，以便查看并重新启用已停用分类。普通成员请求
该参数会返回 `403`，不能借此枚举停用分类。

金额 API 和数据库统一限制在 JavaScript 安全整数区间
`[-9007199254740991, 9007199254740991]`；交易、候选和分摊等必须为正数的字段还要求
大于零。`0003_safe_minor_ranges` 会为全部 money minor 列安装数据库 `CHECK`。迁移前先在
生产库审计历史行；任何非零结果都应先人工修复，不能跳过约束：

```sql
SELECT 'accounts.opening_balance_minor' AS field, count(*) AS unsafe
FROM accounts WHERE opening_balance_minor NOT BETWEEN -9007199254740991 AND 9007199254740991
UNION ALL SELECT 'transactions.amount_minor', count(*) FROM transactions
WHERE amount_minor NOT BETWEEN 1 AND 9007199254740991
UNION ALL SELECT 'transactions.base_amount_minor', count(*) FROM transactions
WHERE base_amount_minor NOT BETWEEN 1 AND 9007199254740991
UNION ALL SELECT 'entry_lines.debit_minor/credit_minor', count(*) FROM entry_lines
WHERE debit_minor NOT BETWEEN 0 AND 9007199254740991
   OR credit_minor NOT BETWEEN 0 AND 9007199254740991
UNION ALL SELECT 'entry_lines.account_amount_minor', count(*) FROM entry_lines
WHERE account_amount_minor IS NOT NULL
  AND account_amount_minor NOT BETWEEN 1 AND 9007199254740991
UNION ALL SELECT 'import_candidates.amount_minor/base_amount_minor', count(*)
FROM import_candidates
WHERE amount_minor NOT BETWEEN 1 AND 9007199254740991
   OR (base_amount_minor IS NOT NULL
       AND base_amount_minor NOT BETWEEN 1 AND 9007199254740991)
UNION ALL SELECT 'split_shares.share_minor', count(*) FROM split_shares
WHERE share_minor NOT BETWEEN 1 AND 9007199254740991;
```

数据库列约束不能表达跨分录余额聚合，所以写入服务会按固定账户 ID 顺序获取行锁，并在
新建、删除、恢复、批量确认和整批撤销前校验投影余额。上线迁移前还应运行一次账户余额
汇总并确认所有结果仍在同一安全区间；发现越界应停止部署并修复历史数据。

`0004_category_name_uniqueness` 把分类同级唯一性改为
`(kind, COALESCE(parent_id, ''), name)` 唯一索引；这样根分类的 `parent_id=NULL` 也不能
绕过约束。迁移前应先查询并合并重复的同类型、同父级、同名称分类，否则迁移会拒绝创建索引。

```sql
SELECT kind, COALESCE(parent_id, '') AS parent_key, name, count(*) AS duplicates
FROM categories
GROUP BY kind, COALESCE(parent_id, ''), name
HAVING count(*) > 1;
```

批量确认会先收集本批全部目标账户，再按账户 ID 一次性加锁，避免两个跨账户批次以相反
顺序入账时形成死锁；确认前还会在同一事务内重新检查完全重复，不能依赖上传时的一次快照。
完全重复的时间条件直接进入 SQL，不再从任意 20 条相似交易中二次筛选。退款创建和从回收站
恢复都会锁定原支出，重新检查原支出仍有效、当前
成员仍可见以及累计退款未超额；恢复时分类和损益分录以原支出的当前值为准。账户与分类的
版本更新同样先锁目标行，过期 `expectedVersion` 必须返回 `409`，不能静默覆盖。

跨账目与分摊的写操作统一按“账目 -> 分摊”加锁；创建分摊也会锁原账目，从而与软删除
串行。原支出改分类前会按退款 ID 锁住全部活动退款，避免分类、删除与版本号相互覆盖。
如果任一退款位于锁定账期，分类同步整体拒绝并回滚。所有账本写入、整批确认/撤销以及账期
锁的创建/删除，都会先获取同一 PostgreSQL transaction advisory lock，再获取业务行锁；这会
消除“写入刚检查完、另一请求立刻关账”的 READ COMMITTED 并发穿透。已有任意锁定期间后，
也禁止再用非零期初余额创建账户，避免无生效日期的期初值追溯改写历史。
整批撤销导入时同样先锁定批次账目；只要其中一笔已有活动退款、已有分摊、后来被修改、
当前成员已无管理权限或位于锁定账期，就整体返回 `409`，不会留下孤立退款或部分撤销。
混合上传中如果已确认 CSV 候选但仍有 PDF/图片等待 OCR，批次保持 `needs_ocr` 警告态，不能
误报 `posted`；已实际入账的部分仍可整批撤销，纯 OCR 且没有账目的批次不可撤销。

PDF 与图片可由可选的 Codex CLI OCR 工作器处理。API 只在上传事务提交后投递任务；可信的
dispatcher 解密并把页面渲染到临时卷，隔离的 runner 只看到这些临时图片和独立
`CODEX_HOME` 登录卷，看不到 PostgreSQL、Redis、源文件密钥或备份密钥。Codex 以
`--ephemeral --ignore-user-config --ignore-rules --sandbox read-only` 运行，并显式关闭 shell、
应用、浏览器、电脑控制、记忆和多代理等工具。模型只能返回固定 JSON Schema；后端仍会重新
校验金额、日期、币种、条数和重复项。OCR 结果只生成待人工确认候选，永不自动入账；超时、
凭证失效、无结果或格式异常都保留为可见失败/待处理状态。生产登录凭证只能写入服务器的
Docker `codex_auth` 卷，禁止复制进仓库、镜像、发布包、日志或聊天记录。
大型 PDF 按 `HOMEDATABASE_OCR_PAGES_PER_JOB` 页串行分批识别，默认整份最多 200 页、
每批 10 页；每批渲染图片收到结果后立即清理，且只有所有批次均成功后才一次性生成候选，
不会因后半段失败留下部分候选。单次模型输出仍由 JSON Schema 限制为最多 500 条，整份
文件的累计后端上限默认 5000 条。

所有数据库时间统一按 UTC 存储，ORM 读出后必须携带 UTC 时区；这条规则在 SQLite
开发库和 PostgreSQL 生产库中一致。账期判断再转换为 `Asia/Shanghai` 的本地日期，导入
候选确认不会发生二次时区偏移。API 中 `Transaction.baseCurrency` 明确
`baseAmountCents` 的单位，`Split.currency` 明确分摊金额沿用原交易币种；跨币种汇总只能
聚合基础币金额，账户流水则应使用分录的 `accountAmountCents/accountCurrency`。API 分录
以 `accountId` 表示真实/脱敏账户，以独立 `ledgerCode` 表示 `expense:*`、`income:*` 等损益
科目；两者不能再混装到同一字段。

PostgreSQL 的 XLSX 导出使用独立 `REPEATABLE READ` 只读会话，让交易、分类、账户、期初
余额和权限判断来自同一数据库快照。SQLite 仅用于开发测试，不作为生产并发保证。

首版迁移在 PostgreSQL 安装 `DEFERRABLE INITIALLY DEFERRED` 约束触发器：每个仍存在的交易在提交时必须至少有两条分录，且基础币借贷合计相等。SQLite 会跳过这段 PostgreSQL 专用 DDL；CI 会在真实 PostgreSQL 服务中验证合法提交、非平衡提交、单分录提交、删分录和级联删交易。

数据库备份成功返回前会完成 AES-256-GCM 加密、SHA-256、解密与结构校验；创建过程全局串行，按实际输出预留磁盘，并在下一次创建时回收中断记录与过期孤儿文件。PostgreSQL 的结构校验使用 `pg_restore --list`，它不是完整恢复演练；正式上线仍必须恢复到隔离数据库并核对关键数据。

部署时先运行 `alembic upgrade head`，再启动 API；不要在生产启用自动建表。生产启动前必须满足：PostgreSQL、关闭开发身份旁路、Secure Cookie、正式 HTTPS 域名对应的 WebAuthn RP/Origin/Host、登录限流与 JSON 请求体上限、Redis/Celery 独立进程、可执行的 `pg_dump`/`pg_restore`、来源文件与数据库备份使用两把不同的 32 字节随机密钥（URL-safe Base64，经秘密管理器注入）、数据库迁移完成。生产数据库升级前应先做可恢复备份，并在同版本预演 `upgrade -> downgrade -> upgrade`。
