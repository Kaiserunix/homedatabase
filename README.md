# HomeDatabase 家庭账本

可自行部署的家庭收支数据库，提供手机 PWA、桌面管理台、账户与分类、账单导入、分摊、报表和备份。后端为 FastAPI / SQLAlchemy，前端为 React / Vite；本地试用使用 SQLite，正式部署使用 PostgreSQL。

**这是公开脱敏版：示例库从零生成，所有交易均为虚构，没有使用真实流水的日期、金额或对手方。** 仓库包含源码、迁移、测试、部署模板和示例数据，不包含运行中的数据库、原始账单、登录凭据、密钥或历史开发记录。

- [接入自己的数据：空库初始化、账单导入、API 和 PostgreSQL](docs/CONNECT_YOUR_DATA.md)
- [示例数据库与 CSV 模板](examples/README.md)
- [公开数据范围与脱敏说明](docs/PUBLIC_DATA.md)
- [服务器部署](infra/README.md) · [日常使用](docs/release/PARENTS_GUIDE.md)

## 本地启动

准备 Python 3.12+、[uv](https://docs.astral.sh/uv/getting-started/installation/)、Node.js 22+ 和 Git。下面的命令可在 PowerShell、Bash 或 zsh 中执行。

```sh
git clone https://github.com/Kaiserunix/homedatabase.git
cd homedatabase/backend
uv sync --locked --group dev
uv run python scripts/setup_local.py --mode personal --directory data/personal
```

初始化会分别询问 `dad`、`mom`、`son` 的密码（至少 12 位，三人不同），创建空账本、初始账户和分类，并生成两把本地加密密钥。`son` 为家庭管理员。此版本固定这三个成员身份；成员数量和身份不能在页面里任意扩展。

```sh
uv run --env-file data/personal/.env uvicorn homedatabase_api.main:app --app-dir src --host 127.0.0.1 --port 8000
```

再打开一个终端，从仓库根目录启动前端：

```sh
cd frontend
npm ci --no-audit --no-fund
npm run dev -- --host 127.0.0.1
```

打开 [手机界面](http://localhost:5200/) 或 [桌面管理台](http://localhost:5200/admin.html)，使用刚设置的账号密码登录。前端默认通过 `/api` 连接真实后端，不需要配置 mock 或 Redis；本地配置使用同步任务。

想先体验虚构账本，把初始化命令换为：

```sh
uv run python scripts/setup_local.py --mode demo --directory data/demo
uv run --env-file data/demo/.env uvicorn homedatabase_api.main:app --app-dir src --host 127.0.0.1 --port 8000
```

演示模式为每个账号生成不同的随机密码，只在本机终端显示一次。演示库有 2026 年 8–9 月的 6 笔虚构账单；查看报表时选择对应月份。正式使用时另建 `data/personal` 空库。初始化脚本拒绝覆盖已经存在的目录。

## 导入自己的账单

1. 登录自己的账号，在“账户”中创建或选择对应的人民币账户。
2. 手机端从“我的”进入“导入账单”，或在桌面端打开导入页面。
3. 上传 CSV / XLSX，选择目标账户，等待生成候选记录。
4. 检查日期、金额、收支、分类和重复提示，确认后才写入账本。

可以直接填写 [CSV 空模板](examples/import-template.csv)，也可以参考 [虚构样例](examples/transactions.csv)。表格金额单位是**元**，API 的 `amountCents` 单位是**分**。转账、退款和外币处理限制见[接入说明](docs/CONNECT_YOUR_DATA.md)。

PDF / 图片识别需要另行启用 OCR；默认本地启动不调用 AI。OCR 启用后图片会交给所配置的模型处理，配置方法见部署文档。

## 目录

```text
backend/       API、数据库模型、迁移、测试、初始化脚本
frontend/      手机 PWA 和桌面管理台
examples/      纯虚构 SQLite 库、SQLite 表结构、CSV 样例与空模板
infra/         PostgreSQL / Redis / Caddy 的 Docker Compose 部署
docs/          自有数据接入、公开范围、使用与部署说明
openapi.yaml   API 契约
```

公开版只收录当前前后端主版本；旧 Streamlit 原型与私人运行目录未包含在内。

## 验证

```sh
cd backend
uv run pytest
uv run python scripts/check_public_demo.py
cd ../frontend
npm run build
npm test
npm run test:budget
```

浏览器端到端测试另需启动真实后端并设置本地测试账号，见 `frontend/playwright.config.ts`。SQLite 适合本地试用；公网部署请按 [infra/README.md](infra/README.md) 使用 PostgreSQL、HTTPS 和独立凭据。
