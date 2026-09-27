# 示例数据

- `demo.sqlite3`：按当前迁移新建的 SQLite 示例库，3 个虚构成员、6 个初始账户、12 个分类和 6 笔虚构交易。可用 SQLite 工具查看。
- `schema.sql`：SQLite 表、索引定义，便于查看数据模型；PostgreSQL 请使用 Alembic 迁移。
- `transactions.csv`：4 条虚构账单，用于体验导入与候选确认。
- `import-template.csv`：只有表头，填写自己的账单后导入。

示例数据库的所有密码哈希为空，登录会失败；会话、Passkey、源文件、导入记录、备份和审计表均为空。运行本地演示初始化时，会复制到独立目录并生成新密码：

```sh
cd backend
uv run python scripts/setup_local.py --mode demo --directory data/demo
```

不会直接修改仓库中的 `demo.sqlite3`。账目日期固定在 2026 年 8–9 月。所有人使用同样的虚构日期、金额和商户，与任何真实家庭无关。

从源码生成另一个全新示例库（输出路径必须不存在）：

```sh
uv run python scripts/build_demo_database.py --output data/rebuilt-demo.sqlite3
uv run python scripts/check_public_demo.py
```

生成器仅使用脚本内固定的虚构条目，不读取既有账本。需要自己的数据时使用 `--mode personal` 从空库开始，详见[接入说明](../docs/CONNECT_YOUR_DATA.md)。
