# AIDetector Backend V2.0

`AIDetector-Back` 是 AIDetector V2.0 的后端服务，当前正式对外提供：

- 用户注册 / 登录 / 游客 token
- 文本检测
- 配额管理
- 历史记录
- PDF 报告
- API Key
- 管理后台接口
- 团队接口
- 健康检查与就绪检查
- RepreGuard RoBERTa v2.0 检测接入

当前仓库根目录只保留这套文档结构：

- [`README.md`](./README.md)：V2.0 总说明
- [`contract/openapi.yaml`](./contract/openapi.yaml)：正式契约
- [`contract/changelog.md`](./contract/changelog.md)：契约版本变更
- [`docs/detection-contract.md`](./docs/detection-contract.md)：检测语义说明
- [`docs/deploy-cutover-checklist.md`](./docs/deploy-cutover-checklist.md)：上线切换清单

本地调试启动：

```bash
docker compose up -d --build
```

数据库迁移：

```bash
docker compose run --rm --no-deps --env-from-file .env.ops api alembic upgrade head
```

## V2.0 边界

### 已开放

- `scan`
- `history`
- `reports`
- `auth`
- `quota`
- `admin`
- `keys`
- `teams`

### 未开放

- `polish`
- `translate`
- `citation`
- `billing`
- `contact`
- `qa`

说明：

- 响应结构里保留的 `translation / polish / citations` 只是兼容保留字段
- 不代表这些能力已经进入 V2.0 正式产品面

## API 4.0.0 检测契约（产品 V2.0）

- `POST /api/v1/detect` 是正式检测入口；`/api/v1/scan/detect`、`/api/v1/scan`、`/api/scan/detect`、`/api/scan` 是兼容入口，五者走同一套检测实现。
- 五个入口硬切要求 UUID `Idempotency-Key`：新逻辑请求生成新 key，同一次请求重试复用原 key；处理中返回带 `Retry-After` 的 `409`，请求冲突返回 `409`，结果无法回放返回 `410`。
- 后端不再接收或解析 PDF、DOCX、TXT 原始文件；旧 `POST /api/v1/detections/parse-files` 已硬删除并返回 `404`，当前浏览器只提交本地抽取后的文本。
- 下游 RepreGuard 必须返回 `score_type="probability"`；缺失或非法分数、标签、阈值会转成 `INVALID_DETECT_RESPONSE`。
- 后端调用 RepreGuard 的 `/detect`、`/health` 和 readiness probe 时统一携带 `X-RepreGuard-Token`；Token 至少 32 个可打印 ASCII 字符，必须独立生成，不能复用用户 JWT 或后端 `SECRET_KEY`。
- RepreGuard 响应按实际数据流限制为 128 KiB；401/403 不透传检测端内部信息，统一转换成 `DETECT_SERVICE_AUTH_FAILED`。
- AI / HUMAN 标签、摘要百分比和段落高亮统一按检测端返回的 `threshold` 解释，不再沿用旧的 `0.34 / 0.67` 概率分档。
- 公共标签与摘要只返回 AI / Human；旧三分类历史在读取时折叠进 Human，数据库原始记录不会被迁移或改写。
- 后端分段保留原始空白和缩进，避免代码、JSON、路径类文本在送检前被展示层 normalize。
- 配额统计优先使用 `quota_usage` ledger；手工历史记录不再隐式消耗 quota。

## 运行结构

Evidence V1 已完成本地计算、模式控制、整文 Router HTTP 调用和失败隔离，以及 EV4-04 的快照持久化。
默认 `DETECT_EVIDENCE_MODE=off`；应用启动持有一个消费实例，off 不读 Bundle、不执行 Evidence。
shadow 执行内部分析并保存快照，不公开结果；serve 在标准检测响应、历史和幂等回放中输出有效的服务端 `evidence`。
没有快照或快照非法时省略该字段，off/shadow 也不会新增 `evidence: null`，已有主检测字段保持原样。
检测列表和管理员原始元数据过滤服务端 Evidence；旧 scan、PDF、示例保持原合同。
主检测确定结果后、数据库结算前，对原始整文只路由一次，不重试；复用既有连接池、鉴权和 128 KiB 响应上限。
`DETECT_EVIDENCE_TIMEOUT_SECONDS=12` 覆盖 HTTP 与本地比较；有效启用时默认 lease 从 150 秒增至 162 秒，off 不变。
后端每进程最多一个 Evidence 任务、不排队；超时仅降级 Evidence，仍占位至实际计算结束，主分数/阈值/标签不变。
合法 Evidence 与 `artifactVersion` 存入既有 `meta_json`，和检测结果、配额、幂等完成状态同事务提交；首次响应使用同一份快照。
历史和幂等回放不重算，旧快照不依赖当前 Bundle；非法快照只省略，实际数据库失败仍按原规则整体回滚。
加载与响应校验只用标准库；特征提取按需加载 `numpy>=2.2,<2.3`、`jieba==0.42.1`，
复用冻结的研究算法与八语言 golden，不需要挂载研究源码或加载模型。
`extract_features` 返回内部特征；`analyze` 保留 22 项观察值、近似百分位、覆盖率及逐项参考差异提示，
不重新判断 AI/Human。当前 Bundle 的参考数据最多支持 19/22 项比较，因此正常也会是 `partial`。
本机真实 tokenizer、主模型、Router、Bundle 与隔离 SQLite 的业务链已通过；测试自建临时服务并退出，不需要手动启动后端。
配置、响应合同与验证命令见 [检测契约第 10 节](docs/detection-contract.md#10-evidence-v1-本地计算与模式输出合同)。

推荐结构：

1. 前端静态文件由 Nginx / Caddy 提供
2. 后端 API 只监听服务器本机 `127.0.0.1:8020`
3. 反向代理统一把 `/api/...` 转发到 `127.0.0.1:8020`
4. 数据库不暴露宿主机端口

正确流量路径：

```text
Browser -> https://your-domain.example
Browser -> https://your-domain.example/api/... -> reverse proxy -> 127.0.0.1:8020
API -> db:5432
```

不要让浏览器直接请求 `localhost:8020`，那是用户自己的电脑，不是你的服务器。

## 环境文件

运行时只保留两套模板：

### `.env.example`

给 API 容器 / 应用运行时使用。

核心字段：

```env
ENVIRONMENT=development
SECRET_KEY=replace-with-a-long-random-secret-at-least-32-chars
POSTGRES_HOST=db
POSTGRES_PORT=5432
POSTGRES_USER=aidetector_app
POSTGRES_PASSWORD=replace-with-a-strong-app-password
POSTGRES_DB=AIDetector
BACKEND_CORS_ORIGINS=http://localhost:3000,http://localhost:5173,http://127.0.0.1:3000,http://127.0.0.1:5173
DETECT_SERVICE_URL=http://host.docker.internal:9000
DETECT_SERVICE_DETECT_URL=
DETECT_SERVICE_HEALTH_URL=http://host.docker.internal:9000/health
DETECT_SERVICE_TIMEOUT=60
REPRE_GUARD_SERVICE_TOKEN=replace-me
```

`REPRE_GUARD_SERVICE_TOKEN` 必须与检测端进程使用的值完全一致。缺失、少于 32 字符或包含非 ASCII 字符时，API 会拒绝启动。
可用 `python -c "import secrets; print(secrets.token_urlsafe(32))"` 生成一次，然后把结果分别写入后端 `.env` 和 RepreGuard 项目根目录 `.env`；容器或服务管理器显式注入的环境变量仍可覆盖文件值。

### `.env.ops.example`

给数据库初始化脚本和部署脚本使用。

核心字段：

```env
POSTGRES_USER=postgres
POSTGRES_PASSWORD=replace-with-a-different-strong-admin-password
POSTGRES_DB=AIDetector
```

### 实际使用

```bash
cp .env.example .env
cp .env.ops.example .env.ops
```

## 数据库账号分工

### `postgres`

数据库管理员账号，用于：

- 建库
- 建用户
- 改密码
- 改权限
- 跑高权限迁移
- 紧急修数

### `aidetector_app`

业务运行账号，用于：

- API 日常增删改查
- 线上长期连接数据库


## 本地开发

### 准备

```bash
cp .env.example .env
cp .env.ops.example .env.ops
```

如果你需要本机数据库客户端直连 PostgreSQL，保留本地专用：

- `docker-compose.override.yml`

它只应该存在于本地开发环境，不应该进入生产目录。

### 启动

PyCharm / PowerShell 本地调试直接用 Docker Compose：

```powershell
docker compose up -d --build
```

如果你想拆成两个 PyCharm 一键配置：

```powershell
docker compose build api
docker compose up -d db api
```

数据库迁移单独建一个 PyCharm 一键配置：

```powershell
docker compose run --rm --no-deps --env-from-file .env.ops api alembic upgrade head
```

这条迁移命令会把 `.env.ops` 里的管理员数据库账号注入到一次性 `api` 容器里，避免用 `.env` 里的业务账号跑高权限迁移。

如果你要完整启动并顺便做环境校验，也可以用：

```bash
./scripts/server-up.sh
```

常用检查：

```bash
docker compose ps
docker compose logs -f db
docker compose logs -f api
curl http://127.0.0.1:8020/api/v1/health
curl http://127.0.0.1:8020/api/v1/ready
```

### 开发检查

```bash
cd backend
python -m pytest tests -q
python -m ruff check app tests
```

## V2.0 生产部署

### 1. 准备配置

```bash
cp .env.example .env
cp .env.ops.example .env.ops
```

然后至少改成：

`.env`

```env
ENVIRONMENT=production
SECRET_KEY=至少32位强随机串
POSTGRES_HOST=db
POSTGRES_PORT=5432
POSTGRES_USER=aidetector_app
POSTGRES_PASSWORD=强业务密码
POSTGRES_DB=AIDetector
BACKEND_CORS_ORIGINS=https://你的域名
DETECT_SERVICE_URL=https://你的RepreGuard地址
DETECT_SERVICE_DETECT_URL=
DETECT_SERVICE_HEALTH_URL=https://你的RepreGuard地址/health
REPRE_GUARD_SERVICE_TOKEN=独立生成的至少32位强随机串
```

生产环境实际使用的 detect / health 端点必须是 HTTPS 且同源；旧 `.php` 检测地址只保留开发兼容，不能作为生产配置。

`.env.ops`

```env
POSTGRES_USER=postgres
POSTGRES_PASSWORD=强管理员密码
POSTGRES_DB=AIDetector
```

### 2. 端口建议

当前推荐端口映射和线上部署保持一致：

```yml
ports:
  - "${API_HOST_BIND:-127.0.0.1}:${API_HOST_PORT:-8020}:8000"
```

也就是默认只监听宿主机本机 `127.0.0.1:8020`，然后由 Nginx / Caddy 转发 `/api` 到 `127.0.0.1:8020`。

### 3. 启动

本地 / 生产都可以直接用 Docker Compose：

```powershell
docker compose up -d --build
```

迁移：

```powershell
docker compose run --rm --no-deps --env-from-file .env.ops api alembic upgrade head
```

更新代码后：

```powershell
git pull --ff-only
docker compose up -d --build
docker compose run --rm --no-deps --env-from-file .env.ops api alembic upgrade head
```

如果你需要自动检查 `.env`、账号授权、迁移和 `health / ready`，再用脚本：

```bash
./scripts/server-update.sh
```

### 4. 必须记住

- 生产目录不要带 `docker-compose.override.yml`
- 数据库不要暴露宿主机端口
- 后端不要直接把 `8000/8020` 开给公网
- 反代层统一处理 `/api`
- `.env` 只放运行账号
- `.env.ops` 只放管理员账号
- 后端与 RepreGuard 使用同一个独立 `REPRE_GUARD_SERVICE_TOKEN`

## 手工维护

### 用管理员账号进库

```bash
docker compose exec db psql -U postgres -d AIDetector
```

### 手工跑管理员迁移

```powershell
docker compose run --rm --no-deps --env-from-file .env.ops api alembic upgrade head
```

## API 4.0.0 本地验收清单

- `docker compose ps`
- `/api/v1/health`
- `/api/v1/ready`
- `/api/v1/detect`（携带 UUID `Idempotency-Key`）
- `/api/scan`（携带 UUID `Idempotency-Key`）
- 旧 `/api/v1/detections/parse-files` 返回 `404`
- 注册 / 登录
- 游客检测
- 登录后检测
- 历史记录
- PDF 导出
- API Key 自测
- 管理后台权限

上线切换细节看：

- [docs/deploy-cutover-checklist.md](./docs/deploy-cutover-checklist.md)

## 后续优化方向

### 近线优化

- 把认证限流从内存版切到 Redis
- 明确信任代理 IP，再安全地读取真实用户 IP
- 把生产反代配置模板收成一份标准 Nginx / Caddy 配置
- 给 `server-up.sh / server-update.sh` 增加更强的失败回滚和环境校验

### 产品能力

- 句级检测稳定化
- 润色建议改成 patch / diff 形式
- 引用核查和证据链
- 文档版本化工作流
- 更完整的结构化导入导出

### 工程能力

- 补 E2E 浏览器回归
- 强化 OpenAPI 驱动的前后端类型同步
- 收口隐藏接口和遗留占位路径

更细的检测语义说明看：

- [docs/detection-contract.md](./docs/detection-contract.md)
- [contract/openapi.yaml](./contract/openapi.yaml)
- [contract/changelog.md](./contract/changelog.md)
