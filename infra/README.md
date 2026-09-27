# HomeDatabase 双机部署骨架

这套 Compose 把账本权威数据留在国内 2 GB 主机：手机 PWA、桌面管理台、API、PostgreSQL、Redis、加密后的源文件与数据库备份都在国内机。`web` 容器使用 Caddy 同域提供页面和 `/api` 反代，并自动处理 HTTPS。海外 8 GB 主机只启动 `heavy` 队列、并固定 `concurrency=1`；它没有 PostgreSQL 地址、数据库密码、源文件密钥或备份密钥。

## 明确边界

| 内容 | 国内机 | 海外机 |
| --- | --- | --- |
| 手机 PWA / 桌面管理台 / HTTPS | 是 | 否 |
| API / 身份认证 | 是 | 否 |
| PostgreSQL 权威账本 | 是；无宿主机端口 | 否 |
| Redis broker | 是；只绑定私网隧道 IP | 仅作为客户端访问 |
| 导入源文件与源文件密钥 | 是 | 否 |
| AES-256-GCM 数据库备份与备份密钥 | 是 | 否 |
| Codex OCR 登录凭证 | 可选；独立 `codex_auth` 卷 | 否 |
| Codex OCR runner | 可选；仅临时图片和公网出口 | 否 |
| `heavy` worker | 否 | 是，单并发 |

`heavy` worker 的 `homedatabase.deployment_health` 仍只验证 Redis 到 Celery worker 的任务往返。PDF/图片 OCR 是独立的国内 `ocr` profile：可信 dispatcher 才能解密源文件；Codex runner 只挂载一次性图片 tmpfs 与独立登录卷，不含数据库、Redis、源文件或备份密钥。OCR 结果只进入候选区，必须由人确认后才入账。

## 网络边界

- PostgreSQL 和 Redis 都没有公网监听方案。PostgreSQL 在 Compose 内网且完全不发布端口。
- Redis 的宿主机绑定地址默认是 `127.0.0.1`。只有 WireGuard/Tailscale 等私网隧道建好后，才把 `HOMEDATABASE_TUNNEL_BIND_IP` 改成国内机的隧道 IP，例如 `10.88.0.1`。
- 绝对不要把 `HOMEDATABASE_TUNNEL_BIND_IP` 设为 `0.0.0.0`、公网 IP，或在云安全组对公网开放 `6379`。主机防火墙应只允许海外 worker 的隧道 IP 访问该端口。
- `web` 服务直接发布 80/443，提供生产 PWA、`/admin.html` 和同源 `/api`；Caddy 自动申请 HTTPS 证书。API 仍只额外发布到国内机 `127.0.0.1:8000` 供本机维护探测，不直接向公网开放。

## 密钥准备

在安全终端分别生成三个不同值；不要提交 `.env`，也不要把备份密钥放在备份目录旁边：

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))" # PostgreSQL 密码
python -c "import secrets; print(secrets.token_urlsafe(32))" # Redis 密码
python -c "import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip('='))" # 源文件密钥
python -c "import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip('='))" # 备份密钥
```

数据库和 Redis 密码应只含 URL-safe 字符，因为它们会进入连接 URL。国内机的私有 `infra/.env.domestic` 至少包含：

```dotenv
HOMEDATABASE_IMAGE=registry.example/homedatabase-api:2026-08-05.1
HOMEDATABASE_WEB_IMAGE=registry.example/homedatabase-web:2026-08-05.1
HOMEDATABASE_PUBLIC_HOST=finance.example.com
HOMEDATABASE_DB_PASSWORD=<URL-safe PostgreSQL password>
HOMEDATABASE_REDIS_PASSWORD=<URL-safe Redis password>
HOMEDATABASE_SOURCE_ENCRYPTION_KEY=<32-byte URL-safe base64 key>
HOMEDATABASE_BACKUP_ENCRYPTION_KEY=<different 32-byte URL-safe base64 key>
HOMEDATABASE_TUNNEL_BIND_IP=10.88.0.1
HOMEDATABASE_REDIS_TUNNEL_PORT=6379
```

海外机只保存镜像引用和 Redis 凭据；不要复制国内 `.env.domestic`：

```dotenv
HOMEDATABASE_IMAGE=registry.example/homedatabase-api:2026-08-05.1
HOMEDATABASE_REDIS_URL=redis://:<URL-safe Redis password>@10.88.0.1:6379/0
```

镜像标签应不可变，正式环境最好写成 registry digest。容器环境变量不是硬件密钥库；正式上线应由云密钥服务或仅 root 可读的部署文件注入。备份密钥一旦丢失，已有 `.hdbak` 无法恢复；轮换前必须先制定旧备份重加密或保留旧密钥的策略。

## 静态检查与启动

在仓库根目录执行。`docker compose config` 只解析配置，不需要 Docker Engine 正在运行：

```powershell
docker compose --env-file infra/.env.domestic -f infra/compose.domestic.yml config -q
docker compose --env-file infra/.env.domestic -f infra/compose.domestic.yml --profile ocr config -q
docker compose --env-file infra/.env.overseas -f infra/compose.overseas-worker.yml config -q
```

国内机先确认 WireGuard/Tailscale 和防火墙，再启动。`migrate` 必须成功后 API 才会启动：

```powershell
docker compose --env-file infra/.env.domestic -f infra/compose.domestic.yml up -d --build
docker compose --env-file infra/.env.domestic -f infra/compose.domestic.yml ps
curl.exe --fail -H "Host: finance.example.com" http://127.0.0.1:8000/api/health
curl.exe --fail https://finance.example.com/
curl.exe --fail https://finance.example.com/admin.html
curl.exe --fail https://finance.example.com/api/health
```

首次部署且迁移成功后，在有 TTY 的服务器终端初始化三名固定成员；该命令会分别提示输入爸爸、妈妈、儿子的不同初始密码：

```powershell
docker compose --env-file infra/.env.domestic -f infra/compose.domestic.yml run --rm api homedatabase-seed --bootstrap-family
```

### 可选 Codex CLI OCR

先保持 `HOMEDATABASE_OCR_ENABLED=false`，构建 OCR 镜像并在服务器的命名卷里完成 ChatGPT
设备登录。终端只显示设备码；`auth.json` 不得复制到工作目录、部署包或命令输出：

```powershell
docker compose --env-file infra/.env.domestic -f infra/compose.domestic.yml --profile ocr build api ocr-dispatcher ocr-runner
docker compose --env-file infra/.env.domestic -f infra/compose.domestic.yml --profile ocr run --rm ocr-runner codex login --device-auth
docker compose --env-file infra/.env.domestic -f infra/compose.domestic.yml --profile ocr run --rm ocr-runner codex login status
```

登录成功后才把私有 `.env.domestic` 改为 `HOMEDATABASE_OCR_ENABLED=true`，再启动三个相关
服务。首次验收使用合成账单，确认候选金额、时间和商户正确，确认源文件仍是加密归档，并
检查 runner 环境中不存在任何 `HOMEDATABASE_*_KEY`、数据库 URL 或 Redis URL：

`HOMEDATABASE_OCR_MAX_PAGES` 是整份 PDF 的硬上限（默认 200），
`HOMEDATABASE_OCR_PAGES_PER_JOB` 是单次 Codex 调用页数（默认 10）。大型 PDF 会串行分批，
不会同时把整份文件渲染到内存/临时卷；全部批次成功后才统一写入待确认候选。
`HOMEDATABASE_OCR_MAX_TRANSACTIONS` 是整份文件累计上限（默认 5000）；固定 JSON Schema
仍把每一次 Codex 调用限制在最多 500 条，二者不是同一个限制。

```powershell
docker compose --env-file infra/.env.domestic -f infra/compose.domestic.yml --profile ocr up -d api ocr-dispatcher ocr-runner
docker compose --env-file infra/.env.domestic -f infra/compose.domestic.yml --profile ocr ps
docker compose --env-file infra/.env.domestic -f infra/compose.domestic.yml --profile ocr exec -T ocr-runner env
```

停用时先把 `HOMEDATABASE_OCR_ENABLED=false` 并重建 API，然后停止 runner/dispatcher；不要
删除 `codex_auth` 卷，除非明确要撤销登录且已确认以后需要重新认证。账单文字是不可信输入，
所以 runner 显式关闭 Codex 的 shell、应用、浏览器、电脑控制、记忆和多代理工具；只读
sandbox 不是唯一保护层。

命令参数与设备登录方式以 OpenAI 官方的
[非交互模式](https://learn.chatgpt.com/docs/non-interactive-mode) 和
[认证说明](https://learn.chatgpt.com/docs/auth) 为准；本项目固定 CLI 版本并在升级前重跑安全
探针，不能默默跟随 `latest`。

把示例 `Host` 换成 `HOMEDATABASE_PUBLIC_HOST`。生产验收还应从手机实际访问 `https://<正式域名>/api/health`，以覆盖 DNS、TLS 和反向代理链路。

海外机使用与国内机完全相同且已发布的镜像：

```powershell
docker compose --env-file infra/.env.overseas -f infra/compose.overseas-worker.yml up -d
docker compose --env-file infra/.env.overseas -f infra/compose.overseas-worker.yml ps
docker compose --env-file infra/.env.overseas -f infra/compose.overseas-worker.yml exec -T heavy-worker celery -A homedatabase_api.celery_app:celery_app inspect ping --destination=heavy@homedatabase-heavy-overseas --timeout=10
```

要验证自定义任务确实经过 `heavy` 队列，而不是只看进程存活：

```powershell
$taskId = docker compose --env-file infra/.env.overseas -f infra/compose.overseas-worker.yml exec -T heavy-worker celery -A homedatabase_api.celery_app:celery_app call homedatabase.deployment_health --queue=heavy
docker compose --env-file infra/.env.overseas -f infra/compose.overseas-worker.yml exec -T heavy-worker celery -A homedatabase_api.celery_app:celery_app result $taskId
```

预期结果含 `"scope": "broker-and-worker-only"`。这不证明 OCR、文件传输、账本写入或备份恢复可用。

爸妈手机访问 `https://<正式域名>/`，管理员电脑访问 `https://<正式域名>/admin.html`。手机可从浏览器菜单“添加到主屏幕”，无需 APK。

## 资源限额

未启用 OCR 时，国内机常驻容器上限约 1.44 GB：PostgreSQL 640 MB、Redis 160 MB、API
512 MB、Caddy 前端 128 MB；一次性迁移容器最多 384 MB，完成后退出。启用 OCR 会再增加
dispatcher 512 MB、runner 384 MB，并允许最多 512 MB 的按实际写入计费 tmpfs；这些是各自
上限而不是保证同时占满，但 2 GB 主机必须先在合成 PDF 上实测 RSS、tmpfs 和 OOM 事件，
不得仅凭健康检查宣称有余量。OCR 固定单并发，API 仍只有一个 Uvicorn worker。

海外 worker 限 6 GB、2 CPU、`concurrency=1`、`prefetch=1`，并在每 10 个任务后回收子进程。`/tmp` 是 2 GB 的内存临时盘。它不参与当前 Codex OCR，也不持有 OCR 登录凭证。

## 备份与回滚

- API 容器包含与 PostgreSQL 17 匹配的 `pg_dump`。明文 dump 只写入容器 `/tmp` 的 tmpfs，加密完成后才把 `.hdbak` 落到国内 `encrypted_backups` 卷；卷本身不是异地备份。创建过程由 PostgreSQL advisory lock 串行化，按本次实际 dump 大小预留安全余量，并在返回成功前完成解密与结构校验。下次创建会把中断中的记录置为失败并清除对应残片；无数据库引用的其他密文经过默认 300 秒保护期后回收，可用 `HOMEDATABASE_BACKUP_ORPHAN_GRACE_SECONDS` 调整。
- 仓库提供 `infra/systemd/homedatabase-backup.{service,timer}`，安装后每小时调用一次国内 `backup` 维护容器。必须先把其中的 `/opt/homedatabase` 改为服务器实际仓库路径，再复制到 `/etc/systemd/system/`、执行 `systemctl daemon-reload` 和 `systemctl enable --now homedatabase-backup.timer`，并检查一次 `journalctl -u homedatabase-backup.service`。只有定时器真实启用且连续观测成功后，才可声称达到约一小时 RPO。
- 每次备份通过解密与结构校验后自动执行轮换：默认保留最近 48 个小时版本、最近 30 天每天一个版本、最近 12 个月每月一个版本；低于 512 MiB 可用空间时拒绝启动新备份。可通过 `HOMEDATABASE_BACKUP_KEEP_*` 与 `HOMEDATABASE_BACKUP_MIN_FREE_BYTES` 调整，但不得把所有保留数都压成零；仍需监控宿主机真实磁盘。
- 管理员 API 与维护容器都会执行 `pg_dump -> AES-256-GCM 加密 -> SHA-256 记录`；还需安排本地电脑定期拉取**密文**，并实际做一次隔离恢复演练。
- 导入源文件默认保留 90 天且加密；它们不应进入数据库备份。删除策略上线前要验证。
- `infra/systemd/homedatabase-retention.{service,timer}` 每天调用维护容器，删除到期密文，并把来源行中的原始载荷替换为仅含结构版本、脱敏标记与问题代码的最小元数据；审计记录继续保留。与备份 timer 一样，安装前先修正 `WorkingDirectory`，启用后检查日志、真实密文消失和数据库原始载荷已脱敏。
- 部署前先生成并校验新备份，记录当前镜像 digest 和 Alembic revision。仅当数据库迁移向后兼容时，才能直接把 API/worker 镜像回退到旧 digest。
- 若迁移不向后兼容，停止写入、保留故障现场，使用对应版本工具从已验证密文备份恢复；不要只回退容器而继续使用新 schema。

首次生产管理员建立、反向代理 TLS、隧道配置、每小时备份调度和恢复演练均属于上线门槛，但不由这两个 Compose 文件自动完成。
