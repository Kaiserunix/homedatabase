# HomeDatabase 演示脚本与验证摘要

> 状态：`Historical`（发布展示与三分钟演示参考；完整功能与边界见 [README](../README.md) 与 [AI 能力边界与路线图](AI_ROADMAP.md)）

## 项目一句话事实

HomeDatabase 是自托管家庭复式账本（FastAPI + SQLAlchemy + React/Vite）：支持手机 PWA 与桌面管理台、CSV/TSV/XLSX 候选复核导入与整批撤销、个人账户自动脱敏、成员分摊协商、5 层级 XLSX 导出、AES-256-GCM 加密备份，以及默认关闭的可选 Codex CLI OCR 工作器。自然语言问账、现金流预测与情景推演属于未实现规划。

## 三分钟本地演示流程（无需配置 AI）

提前按 [README](../README.md) 使用 `--mode demo` 启动后端与前端（演示数据为纯虚构，6 笔交易日期固定在 **2026 年 8–9 月**）：

1. **登录（0:00–0:30）**：打开手机端 `http://localhost:5200/` 或桌面管理台 `http://localhost:5200/admin.html`。首次登录点击 **密码登录** 展开输入框（未绑定 Passkey 前勿直接点 Passkey 按钮），使用终端打印的 `son` 随机密码登录管理员视图。
2. **查看账本与权限（0:30–1:00）**：展示个人账户与共享账户，以及手机端与桌面端连接同一套后端 API。
3. **CSV 导入与候选复核（1:00–2:00）**：进入桌面 **导入与确认**（或手机 **我的 → 导入账单**），选择有管理权限的 CNY 目标账户并上传 `examples/transactions.csv`。演示候选检查、重复提示与确认入账（未确认前不写入账本；入账后支持在无修改/分摊/退款/锁账前提下整批撤销）。
4. **报表与边界说明（2:00–3:00）**：在桌面 **报表导出** 选择自定义区间 `2026-08-01` 至 `2026-09-30` 导出 Tier 5 XLSX；说明可选 OCR（默认关闭，输出同样进候选区）与未实现的 AI 规划边界。

## 仓库可验证基线

- **后端测试**：101 项 pytest 用例（`backend/tests/`）与 3 个 PostgreSQL 专项并发/约束脚本（`backend/scripts/verify_postgres_*.py`）。
- **前端测试**：189 项 Vitest 单元测试（`frontend/tests/unit/`）、TypeScript / Vite 生产构建与产物体积检查（`npm run test:budget`）。
- **公开演示库校验**：`backend/scripts/check_public_demo.py`（检查外键、借贷平衡、空凭据与 12 张运行表为空）。

> 上述为软件工程与构建验证，不代表 OCR 识别准确率。

[文档索引](INDEX.md) · [AI 能力边界与路线图](AI_ROADMAP.md) · [接入自己的数据](CONNECT_YOUR_DATA.md) · [公开数据范围](PUBLIC_DATA.md) · [示例数据](../examples/README.md)
