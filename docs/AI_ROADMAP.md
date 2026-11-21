# HomeDatabase AI 能力边界与路线图

> 状态：`Reference` · 仅说明已实现能力与未来规划边界，不代表已承诺的交付日期。

## 当前已实现 vs 未来规划

| 领域 | 当前代码状态（已验证） | 依据 |
| --- | --- | --- |
| 核心账本与权限 | **已实现**：3 固定成员（`dad` / `mom` / `son`，`son` 为管理员）、个人/共享账户权限与脱敏、整数分复式分录、软删除回收站、期间锁定、分摊协商、5 层级 XLSX 导出、AES-256-GCM 备份与源文件留存 | `backend/src/homedatabase_api/`, `openapi.yaml`（39 个端点） |
| 账单导入与候选复核 | **已实现**：CSV / TSV / XLSX 确定性解析、SHA-256 与精确交易去重、人工候选复核与整批撤销 | `services/import_parsers.py`, `services/imports.py` |
| PDF / 图片 OCR 工作器 | **已实现（默认关闭）**：`HOMEDATABASE_OCR_ENABLED=false`；开启后经隔离的 Codex CLI `ocr-runner` 输出受 JSON Schema 约束的候选，仍须人工确认后入账 | `ocr_contract.py`, `ocr_runner.py`, `services/ocr_tasks.py`, `infra/compose.domestic.yml` |
| 自然语言问账 / 预算预测 / 情景推演 / 月度简报 | **未实现（规划中）**：管理台报表页中的“AI 问账本”与“预算与预测”仅为禁用占位项 | `frontend/src/admin/pages/Reports.tsx` |

## 规划阶段（仅路线，未实现）

| 阶段 | 目标能力 | 依赖的现有架构基础 | 上线前验证门槛 |
| --- | --- | --- | --- |
| 01 · 财务记忆检索 | 按语义检索交易、退款、分摊并回链原始记录 | `transactions`、`entry_lines`、`source_records` 与成员权限边界 | 虚构数据集检查退款/跨月/分摊召回与越权隔离 |
| 02 · 受控自然语言问账 | 自然语言转受边界的只读查询；确定性代码算金额，模型只负责意图与解释 | `openapi.yaml` 只读查询接口、整数分口径、`Asia/Shanghai` 账期 | 带标准答案的评测集校验查询正确性、金额一致性与来源覆盖 |
| 03 · 现金流预测与提醒 | 周期性账单识别（用户确认周期）、90 天资金缺口与预算偏离提醒 | 历史复式流水与基础币 `CNY` 汇总 | 历史时间切分回测，对比简单基线的误差与误报率 |
| 04 · 情景推演 | 对比多套消费/应急金方案的月度现金余额与缺口 | 确定性整数运算与资金守恒约束 | 同输入可复现、跨月边界单测，解释文本与数值一致 |
| 05 · 可选家庭复盘 | 按授权共享范围生成定期提纲与偏好记忆 | 个人账户脱敏规则与成员授权 | 依赖前序阶段准确率达标；共享范围与偏好可查可删 |

## AI 数据边界

- 默认本地启动不调用任何 AI 接口。
- 启用现有 Codex OCR 后，待识别的渲染图片（JPEG）会发送至所配置的 Codex 模型服务；`ocr-runner` 容器仅挂载临时图片卷与独立 `codex_auth` 登录卷，无法访问 PostgreSQL、Redis、源文件密钥或备份密钥。
- 自托管账本不等于本地推理；本地模型适配与最小化上下文裁剪尚未实现。

[文档索引](INDEX.md) · [返回首页](../README.md) · [接入自己的数据](CONNECT_YOUR_DATA.md) · [演示与投稿参考](SUBMISSION.md)
