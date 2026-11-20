# 公开数据范围

> 状态：`Reference` · 说明本公开脱敏快照的边界与自动化校验口径。

本仓库是独立运行的源码快照，使用全新 Git 历史。`examples/demo.sqlite3` 由 `backend/scripts/build_demo_database.py` 从零构建，并非对真实数据库脱敏后的副本。

## 包含

- 当前 FastAPI / React 主版本、Alembic 迁移、自动化测试与 Docker Compose / systemd 部署模板。
- 纯虚构演示库（`examples/demo.sqlite3`）、SQLite 表结构（`examples/schema.sql`）、导入样例（`examples/transactions.csv`）与空模板（`examples/import-template.csv`）。示例库密码哈希清空，演示启动时重新生成本地随机密码。
- 前端开发 mock（`frontend/src/data/mock.ts`）与单元测试 fixture；应用默认直连真实 API（`VITE_USE_MOCKS=false`）。
- 环境变量模板与占位符，不包含有效密钥。

## 排除

- 真实数据库、原始账单（CSV / PDF / 图片）、加密源文件（`*.hdsrc`）与加密备份（`*.hdbak`）。
- 密码、密码哈希、会话 Cookie、令牌、Passkey、Codex 登录凭据（`auth.json`）与真实 `.env`。
- 运行日志、AI 输入输出、测试/截图产物、旧版原型与历史提交。

## 校验与使用注意

`backend/scripts/check_public_demo.py` 验证示例库完整性、外键、`0004_category_name_uniqueness` 迁移版本、3 成员（密码哈希为空且 SQLite 页内无 `scrypt$` 残留）、6 账户、12 分类、6 笔白名单虚构交易、12 条借贷平衡分录及 12 张运行表为空。`.gitignore` 默认忽略 `.env*`、`**/data/`、数据库与密文文件。真实数据请始终放在忽略目录，切勿使用 `git add -f` 提交业务导出文件。

[文档索引](INDEX.md) · [示例数据说明](../examples/README.md) · [接入自己的数据](CONNECT_YOUR_DATA.md)
