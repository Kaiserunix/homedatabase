# HomeDatabase — Product Strategy, Consensus Positioning & Engineering Roadmap

> **Status**: Pod A Cross-Functional Consensus (Tech Lead + Marketing & Growth + Simulated Household & Homelab Users)
> **Scope**: Positioning, honest capability boundaries, conversion architecture, and prioritized P0–P2 engineering roadmap grounded in the current repository codebase.

---

## 1. Executive Positioning & Core Value Proposition

### Mediated Product Identity
- **Headline (CN)**: **HomeDatabase · 自托管家庭复式账本（迈向家庭 AI CFO 的可核对财务底座）**
- **Headline (EN)**: **HomeDatabase — Self-Hosted Double-Entry Household Ledger with Human-Confirmed Bill Staging**
- **One-Line Hook**: 导错一份账单也不会污染账本——用「Staging 候选池防污染复核 + 一键整批无痕撤销 (`undo_batch`) + 整数分复式分录 + 跨私有账户自动脱敏与分摊确认」，把散落的日常账单变成经得起核对的家庭财务记录。

### Why HomeDatabase vs. Firefly III / Actual Budget / Cloud Apps (4 Shipped Differentiators)
1. **防污染导入闭环（Staging + 乐观锁 + 整批无痕撤销）**：CSV/XLSX 与可选 OCR 提取结果均先加密归档源文件并落入 `import_candidates` 候选池（含 SHA-256 文件去重、行哈希去重、已有流水重复检测与 `expectedVersion` `409` 乐观锁）；即使确认入账后发现错误，在未发生退款/分摊/账期锁定前，可通过 `undo_batch` 一键整批安全撤销。
2. **审计级复式底座（整数分 `minor` + PostgreSQL 延迟借贷平衡触发器 + 账期锁定）**：全链路整数分计算（`money.py`, `MAX_SAFE_MINOR`），消除浮点精度漂移与单边烂账。
3. **家庭隐私与共同账本兼得**：个人账户默认私密隔离，跨私有账户往来在保持复式借贷平衡的同时自动脱敏显示为 `私人账户（已脱敏）`；家庭分摊 (`Splits`) 需对方点击「接受」或填写「退回理由」确认，原交易金额改动后自动升级版本并要求重新确认。
4. **五层渐进式 XLSX 审计级快照导出**：在 PostgreSQL `REPEATABLE READ` 事务快照中校验借贷平衡、防御 Excel 公式注入（`strings_to_formulas: False`），并输出覆盖「阅读账单 → 交易明细 → 复式分录 → 期初/期末零差额勾稽 → 统计图表」的五层工作簿。

---

## 2. Three-Layer Architectural Truth Table (Anti-Overpromise Contract)

All external copy, demos, and onboarding guides MUST strictly respect the boundary between shipped capabilities and future roadmap layers:

| Layer | Scope | Current Code Reality | Marketing / Copy Redline |
| :--- | :--- | :--- | :--- |
| **Layer 1 · 可核对的家庭复式账本** | 自托管账本、手机 PWA + 桌面管理台、CSV/XLSX 候选池导入与整批撤销、家庭分摊确认、WebAuthn Passkey、五层 XLSX 报表、加密备份 | **✅ 已交付**（FastAPI + SQLAlchemy + React/Vite；本地 SQLite / 生产 PostgreSQL；101 后端测试 + 189 前端测试） | 作为首屏核心价值与演示主线。明确说明 V1 固定三角色模板（`dad` / `mom` / `son`，`son` 为管理员）与 `CNY` 单基础币约束。 |
| **Layer 2 · 候选池 AI 账单识别工作器** | PDF / 图片账单提取为结构化候选记录，经人工复核后入账 | **⚙️ 已交付，可选开启，默认关闭**（当前 `ocr_runner.py` 仅支持 **Linux Docker + `codex` CLI 设备码登录**，非通用 HTTP Vision API） | 严禁暗示“填入任意 OpenAI/DeepSeek/Ollama Key 即可直接识图”；必须诚实标注依赖 Linux + `codex` CLI 且结果仅进候选池。 |
| **Layer 3 · 带来源追溯的 AI 财务副驾 (AI CFO)** | 自然语言问账、消费异动归因、现金流预测与大额支出情景推演 | **🔭 规划中（当前代码中 0% NLQ / 预测端点）** | **严禁**在已上线功能中宣称“已支持自然语言问账/AI 预测”；规划示例仅可置于路线图章节并明确标示未上线。 |

---

## 3. Mediated Debate Resolutions (Tech × Marketing × Simulated Users)

| Tension Point | Conflicting Signals | Final Consensus Decision |
| :--- | :--- | :--- |
| **“AI CFO” 定位 vs. 0% NLQ 代码现状** | Marketing 希望保留高辨识度“AI CFO”心智；Tech 与模拟用户反对首屏三连免责声明与三句不可用聊天引用块。 | 保留“迈向家庭 AI CFO 的可核对财务底座”长期愿景，将 4 大已交付硬核能力（Staging + `undo_batch` + 私有脱敏/分摊 + 五层 XLSX）提至首屏 C 位，把未上线问答示例移出首屏并收束至 Layer 3 路线图。 |
| **硬编码 `dad`/`mom`/`son` 角色包装** | Marketing 曾提议包装为“三角色家庭权限沙盒”；Tech (`models.py:85` `CheckConstraint`) 与夫妻用户指出仅 `son` 有 `/admin.html` 管理员权限，强行美化会误导二人家庭。 | 拒绝粉饰，坦诚写明 V1 预置 `dad`/`mom`/`son` 三角色且 `son` 为管理员；给二人世界/伴侣家庭明确实操建议（用 `son` 作主理人账号 + `mom`/`dad` 作伴侣账号），并将动态成员迁移列入 P2。 |
| **本地快速启动顺序与 2 个首次体验暗坑** | 原 README 把需手动输 3 个 12 位密码的 `--mode personal` 放第 1 位，埋没 `--mode demo`；且未提示 Passkey 默认收起密码框 & Demo 交易落在 `2026-08 ~ 2026-09`。 | 将 `--mode demo` 调至 Quickstart 第 1 位，并在紧邻位置增加两条高亮避坑提示（首次登录点击「密码登录 ⌄」；查看报表/流水需切至 `2026-08 ~ 2026-09`）。 |
| **候选复核区文案与真实 UI 不一致** | 原文档宣称可在候选区修正日期、金额、收支方向；真实 `Import.tsx` / `ImportDetail.tsx` 与 `patch_candidate` 仅支持修改建议分类、未指定的目标账户及外币汇率。 | 立即修正 `README.md` 描述以 100% 贴合当前 UI，同时把候选区行内编辑与批量改分类纳入 P1 工程清单。 |
| **首屏死链 (`docs/release/PARENTS_GUIDE.md`)** | 仓库无此文件，点击 404 直接击穿家庭用户信任。 | 移除死链，改为直接链接 `docs/CONNECT_YOUR_DATA.md` 与 `examples/README.md`，并在 README 内提供清晰的家人日常上手指引。 |

---

## 4. Prioritized Engineering & Decoupling Roadmap (Reprioritized Before Phase 4/5)

在投入长期的 Phase 4/5 现金流预测与人生情景推演之前，按真实用户阻塞度与架构解耦 ROI 排序如下：

### P0 — Compliance, Trust & Chinese Bill Import Correctness
1. **补齐开源 `LICENSE` 文件**：当前仓库根目录缺少 `LICENSE`（GitHub 识别为全权保留），阻碍社区采用与 PR 贡献。
2. **修复微信支付 CSV 表头抢占与中性交易解析（`backend/src/homedatabase_api/services/import_parsers.py`）**：
   - 修复 `_ALIASES["direction"]` 中 `"交易类型"` 在先到先得匹配下抢占标准微信表头（`交易时间,交易类型,交易对方,商品,收/支,金额(元)`）中第 5 列 `"收/支"` 的 Bug，避免微信红包/群收款因 `missing_or_ambiguous_direction` 被静默丢弃。
   - 抽离可插拔账单解析器（WeChat / Alipay / 通用银行 CSV / OFX），优雅处理中性流水（`不计收支`、提现、信用卡还款、零钱通互转）与退款关联。
3. **修复部署文档与队列配置漂移**：核对 `infra/compose.overseas-worker.yml`（当前 `--queues=heavy` 仅消费 `deployment_health`）与 `ocr_tasks.py`（入队 `queue="ocr"`）的队列一致性。

### P1 — Daily Household UX & Homelab Self-Hosting Ergonomics
1. **历史商户自动分类记忆（Payee → Category Memory）与别名映射**：补充支付宝/微信常见分类别名映射及按历史同名商户自动预填分类 + 候选区批量改分类，消除“导入 200 条全是未分类”的重复点选。
2. **候选区行内修正与日常补记闭环**：
   - 开放候选区修改日期、商户、金额、收支方向与目标账户；
   - 手机端「记一笔 (`Add.tsx`)」与桌面端手动记账增加日期选择器（支持补记历史账单）；
   - 手机首页与桌面仪表盘增加月份切换器；首次未绑定 Passkey 时登录页默认展开密码框。
3. **单机 Homelab 一键 Docker Compose + 可插拔 `VisionExtractor` 接口**：
   - 提供不依赖双机 WireGuard 隧道的单机 PostgreSQL Docker Compose 模板（对比 Actual Budget / Firefly III 30 秒上手体验）；
   - 将 `ocr_runner.py` 抽象为 `VisionExtractor` 接口，在保留 `codex` CLI 的同时支持标准 OpenAI-compatible Vision API（Qwen-VL / DeepSeek / GPT-4o）与本地 Ollama 多模态模型。

### P2 — Household Generalization & Deterministic AI CFO Foundation
1. **解除固定三成员与单币种/单时区硬编码**：通过 Alembic 迁移移除 `models.py:85` 的 `ck_members_identity`（`dad`/`mom`/`son`）限制，支持动态增删成员与自定义管理员；解耦 `base_currency: Literal["CNY"]`（`config.py:30`）与 `Asia/Shanghai`（`import_parsers.py:25`）硬编码。
2. **先做确定性只读财务分析引擎，再接 LLM 叙事层**：先落地纯代码计算的月度环比归因、周期性订阅识别、异常支出检测与净现金流聚合 API，再在其上挂载只读、带交易 ID 来源引用的自然语言问账（确保 AI 绝不编造数字）。

---

## 5. Distribution & Conversion Angles

- **GitHub / Hacker News (`Show HN`)**: *"Show HN: HomeDatabase — Self-hosted household double-entry ledger with integer cents, human-confirmed bill staging, full-batch undo, and privacy-preserving family splits."*
- **V2EX / 小红书 / 自托管社群**: *"为什么我放弃了云端记账 App，给家里写了一个不怕导错账的自托管复式账本（候选池复核 + 一键整批撤销 + 私人账户往来自动脱敏）"*
- **Recommended Visual Assets to Capture Next**:
  1. `staging-review-and-undo.gif` — 15 秒实录：上传 CSV → 候选区去重/分类核对 → 入账 → 一键整批撤销。
  2. `family-privacy-and-splits.png` — 个人私有账户自动脱敏（`私人账户（已脱敏）`）与家庭分摊「接受 / 退回」双端截图。
  3. `xlsx-5-tier-reconciliation.png` — 五层 XLSX 报表的 Tier 4 期初/期末零差额勾稽页截图。
