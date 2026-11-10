# AIDetector Backend

RepreGuard 文本检测平台的后端 API，负责用户与游客会话、检测编排、配额、历史记录和 PDF 报告。服务使用 FastAPI、SQLAlchemy 和 PostgreSQL，主模型推理由独立的 RepreGuard 检测端完成。

- 接收文本，按模型 token 上限分段调用检测端，返回整文结果和段落分析。
- 用幂等请求管理检测任务，保存结果、历史与配额结算。
- 提供注册登录、游客访问、API Key 和管理后台接口。
- 可选启用写作特点分析，将整文路由与参考统计保存为结果快照。

```text
AIDetector-web → AIDetector-Back → RepreGuard
                       ↓
                   PostgreSQL
```

[前端仓库](https://github.com/MoXiao528/AIDetector-web) · [检测端仓库](https://github.com/MoXiao528/repre_guard_server) · [API 契约](contract/openapi.yaml)

## 快速开始

以下本地步骤使用 PowerShell，在仓库根目录执行。需要 Docker Desktop、Docker Compose v2，以及一个已启动、可从 API 容器访问的 RepreGuard 服务。首次检测还需要下载或预先缓存 `DETECT_TOKENIZER_MODEL` 指定的 tokenizer。

### 1. 获取代码与配置

```powershell
git clone -c core.autocrlf=false https://github.com/MoXiao528/AIDetector-Back.git
cd AIDetector-Back
Copy-Item .env.example .env
Copy-Item .env.ops.example .env.ops
```

已有工作目录直接编辑现有配置。Windows 下需让 `scripts/docker/init-runtime-user.sh` 保持 LF 换行，上面的克隆命令会为新仓库设置此行为。两份环境文件的用途如下。

| 文件 | 用途 | 需要填写 |
| --- | --- | --- |
| [`.env`](.env.example) | API 运行配置 | `SECRET_KEY`、业务账号 `aidetector_app` 的密码、检测地址、共享服务 Token |
| [`.env.ops`](.env.ops.example) | 数据库初始化和迁移 | 管理员 `postgres` 的独立密码，数据库名与 `.env` 一致 |

密钥和服务 Token 使用至少 32 位随机值。可用 `python -c "import secrets; print(secrets.token_urlsafe(32))"` 分别生成密钥和两套数据库密码。`REPRE_GUARD_SERVICE_TOKEN` 使用检测端已配置的同一值。

本地 `.env` 中核对以下配置，并保留模板中的数据库等其他字段。

```dotenv
ENVIRONMENT=development
BACKEND_CORS_ORIGINS=http://localhost:5300,http://127.0.0.1:5300
DETECT_SERVICE_URL=http://host.docker.internal:9000
DETECT_SERVICE_DETECT_URL=
DETECT_SERVICE_HEALTH_URL=http://host.docker.internal:9000/health
DETECT_EVIDENCE_MODE=off
```

`host.docker.internal` 用于 Docker Desktop 容器访问宿主机。检测端须监听容器可达的接口，例如 `REPRE_GUARD_HOST=0.0.0.0`。检测端在其他主机或使用 Linux Docker Engine 时，改成容器实际可达的地址。

当前 Compose 将数据卷挂在 `/var/lib/postgresql/data`。新建本地数据库时，在 `.env.ops` 添加以下配置，让 PostgreSQL 18 明确使用该挂载目录。已有数据库须先核对实际数据目录和卷挂载，再确定配置。

```dotenv
PGDATA=/var/lib/postgresql/data
```

### 2. 初始化数据库并启动 API

```powershell
docker compose -f docker-compose.yml up -d db
docker compose -f docker-compose.yml exec db pg_isready -h 127.0.0.1 -U postgres -d AIDetector
```

等待 `pg_isready` 返回 `accepting connections`，再构建镜像、用管理员账号迁移并启动服务。

```powershell
docker compose -f docker-compose.yml build api
docker compose -f docker-compose.yml run --rm --no-deps --env-from-file .env.ops api alembic upgrade head
docker compose -f docker-compose.yml up -d api
```

API 开始监听后检查就绪状态。若连接被拒绝，查看启动日志并等待服务启动后重试。

```powershell
Invoke-RestMethod http://127.0.0.1:8020/api/v1/ready
```

就绪响应应包含 `status=ok`、`database=ok` 和 `detect_service=ok`。API 默认地址是 `http://127.0.0.1:8020`，交互接口文档位于 `/docs`。排查启动问题可运行 `docker compose -f docker-compose.yml logs --tail=100 api db`。

全新数据库会自动创建业务账号。

### 3. 完成一次检测

以下 PowerShell 示例先获取游客凭证，再提交一段满足最小长度的英文文本。执行后会创建检测记录并消耗游客额度。

```powershell
$apiBase = 'http://127.0.0.1:8020'
$guest = Invoke-RestMethod -Method Post -Uri "$apiBase/api/v1/auth/guest"
$headers = @{
    Authorization = "Bearer $($guest.accessToken)"
    'Idempotency-Key' = [guid]::NewGuid().ToString()
}
$text = 'This is a deployment check for the text detection service. The request goes through the application API, reaches the model server, and stores the returned result. We use a complete paragraph so the example meets the minimum input length and exercises the same path as a normal user submission.'
$body = @{ text = $text } | ConvertTo-Json
$result = Invoke-RestMethod -Method Post -Uri "$apiBase/api/v1/detect" -Headers $headers -ContentType 'application/json' -Body $body -TimeoutSec 180
$result | Select-Object detectionId, label, score, currentCredits
```

成功时返回 `detectionId`、`label`、`score` 等字段，具体分数由模型计算。输入须包含至少 200 个非空白字符，总长度不超过 20,000 个字符。每次新检测生成新的 UUID `Idempotency-Key`，同一次请求重试时复用原请求头和正文。

## 生产部署

生产使用 Linux、Docker Compose 和 HTTPS 反向代理。在根目录准备好 `.env`、`.env.ops` 后，核对以下项目。

| 配置 | 生产要求 |
| --- | --- |
| `ENVIRONMENT` | `production` |
| `SECRET_KEY` | 独立生成的至少 32 位强随机值 |
| `POSTGRES_USER` | API 使用 `aidetector_app`，管理员账号放在 `.env.ops` |
| `BACKEND_CORS_ORIGINS` | 前端实际使用的 HTTPS origin |
| 检测地址 | 实际 detect 和 health 地址使用同源 HTTPS |
| `REPRE_GUARD_SERVICE_TOKEN` | 与检测端一致的独立服务令牌 |

Compose 默认将 API 发布到 `127.0.0.1:8020`，PostgreSQL 通过 Compose 内网访问。Nginx/Caddy 将同域 `/api/` 原样转发到 API，长检测请求的代理超时须与后端预算、前端 180 秒超时配合。生产目录应移除开发用的 `docker-compose.override.yml`。

### 检测端地址

直接连接检测服务的 HTTPS 入口时，设置 `DETECT_SERVICE_URL`，留空 `DETECT_SERVICE_DETECT_URL`，将 `DETECT_SERVICE_HEALTH_URL` 指向同源 `/health`。

通过现有 PHP 入口部署时使用以下配置。

```dotenv
DETECT_SERVICE_URL=https://umcat.cis.um.edu.mo
DETECT_SERVICE_DETECT_URL=https://umcat.cis.um.edu.mo/api/aidetect.php
DETECT_SERVICE_HEALTH_URL=
```

PHP 固定转发到检测端 `/detect`，需保留请求、`X-RepreGuard-Token`、响应 JSON 和 HTTP 状态码。健康地址留空后，`/api/v1/ready` 会经 PHP 发起一次短文本主检测。

### 启动与更新

Linux 脚本会按 Bash 语法读取环境文件。将 `.env` 中的应用名称写成 `APP_NAME='AIDetector API'`，其他包含空格的值也需正确引用。

新建数据库时，按快速开始中的 `PGDATA` 配置匹配卷目录，先启动数据库并等待 TCP `pg_isready` 检查成功，再运行下列脚本。更新已有生产库前，先备份并确认实际数据目录、卷挂载与所用 PostgreSQL 版本相符。

```bash
bash scripts/server-up.sh
```

脚本依次启动数据库、设置业务账号权限、构建镜像、执行迁移、启动 API 并检查服务。确认当前分支和本地改动后，执行 `bash scripts/server-update.sh` 拉取跟踪分支并更新。

若服务器使用额外的 Compose 文件，启动、迁移、更新和检查均须指定同一组文件。Linux 脚本可通过进程环境传入，例如 `COMPOSE_FILE=docker-compose.yml:compose.evidence.yml bash scripts/server-up.sh`。此例适用于服务器已有该覆盖文件的情况。

### 部署验收

```bash
docker compose ps
curl -fsS http://127.0.0.1:8020/api/v1/health
curl -fsS http://127.0.0.1:8020/api/v1/ready
curl -fsS https://your-domain.example/api/v1/ready
```

`/health` 验证 API 存活，`/ready` 验证数据库和主检测服务。再通过网页完成游客检测、登录检测、历史查询与 PDF 导出。开启 Evidence 时，还需通过实际 PHP/检测入口验证路由结果和制品 SHA。

### 可选配置

| 功能 | 配置与启用条件 |
| --- | --- |
| 写作特点 | 默认 `DETECT_EVIDENCE_MODE=off`。先启用检测端 Router，再挂载匹配的 Bundle，设置 `DETECT_EVIDENCE_BUNDLE_PATH` 和 `DETECT_EVIDENCE_BUNDLE_SHA256`。`shadow` 保存内部快照，`serve` 对外提供有效结果。前端显示还受 `VITE_EVIDENCE_ENABLED` 控制。 |
| HTTP 演示 | 仅适用于已支持 `ALLOW_INSECURE_HTTP_COOKIES` 的版本，如 `demo/text-envelope` 分支。设为 `true` 后允许同域 HTTP Cookie，仍保留 `ENVIRONMENT=production` 和下游 HTTPS 要求。HTTP 会明文传输凭据与正文。恢复 HTTPS 后关闭开关、重建 API 并重新登录。 |

切回不支持 HTTP 演示配置的版本前，从 `.env`、Compose 和服务环境删除 `ALLOW_INSECURE_HTTP_COOKIES`。

## 开发与测试

本机开发检查使用 Python 3.11，与 CI 和 API 镜像一致。激活 Python 虚拟环境后，从仓库根目录执行以下命令。

```bash
cd backend
python -m pip install -r requirements.txt
python -m ruff check app tests
python -m pytest tests -q
```

普通测试使用隔离的 SQLite 和模拟依赖，真实模型、PostgreSQL 等专项验收需要按各测试说明显式启用。
