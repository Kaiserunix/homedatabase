# 示例数据与导入样例

## 基础示例文件

- `demo.sqlite3`：按当前迁移新建的 SQLite 示例库，3 个虚构成员、6 个初始账户、12 个分类和 6 笔虚构交易。可用 SQLite 工具查看。
- `schema.sql`：SQLite 表、索引定义，便于查看数据模型；PostgreSQL 请使用 Alembic 迁移。
- `transactions.csv`：4 条虚构账单，用于体验导入与候选确认（后端自动化测试也会校验该文件）。
- `import-template.csv`：只有表头，填写自己的账单后导入。

## 多格式账单导入样例（`examples/fixtures/`）

下列样例全部为虚构数据，可直接在导入页上传体验后端解析器与候选复核行为：

| 文件 | 演示场景 | 说明 |
| --- | --- | --- |
| `fixtures/wechat-pay-sample.csv` | 微信风格导出 | 含前导账单摘要元信息行与 `收/支`、`金额(元)` 表头（提示：真实微信导出若在 `收/支` 前包含 `交易类型` 列，需先删除或重命名 `交易类型` 列，避免表头别名先匹配到商户消费描述） |
| `fixtures/alipay-sample.csv` | 支付宝风格导出 | 含前导账号摘要行与 `交易分类`、`收/支` 列 |
| `fixtures/bank-statement-split-columns.csv` | 银行拆分支出/收入列 | 使用 `支出金额` / `收入金额` 双列与千分位引号数字（如 `"1,280.00"`） |
| `fixtures/multi-currency-travel.csv` | 多币种候选与汇率复核 | 含 `USD` / `EUR` / `HKD` / `CNY`；上传到人民币账户时会进入 `needs_fx_review` 状态，需在候选区补录汇率后才能确认 |
| `fixtures/english-ledger.csv` | 英文表头 | 使用 `date,payee,type,amount,currency,category` 标准英文列名 |
| `fixtures/ocr-candidate-sample.json` | 可选 OCR 输出契约样例 | 对齐 `backend/src/homedatabase_api/ocr_contract.py` 的结构化 JSON 示例（仅供开发者/自托管校验契约参考，上传账单请使用 CSV/XLSX 或启用 OCR 后上传 PDF/图片） |

## 本地演示初始化

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
