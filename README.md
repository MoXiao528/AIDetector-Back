# AIDetector Backend

本项目基于 **FastAPI + PostgreSQL + SQLAlchemy 2.0 + Alembic + JWT + bcrypt** 构建，当前实现最小可运行后端与健康检查。

## RBAC 角色

- VISITOR：只读/未登录视角（当前 API 需要认证后才可用）。
- INDIVIDUAL：默认注册用户，拥有常规 API 权限（检测、管理自身 API Key）。
- TEAM_ADMIN：团队管理员（预留，权限高于 INDIVIDUAL）。
- SYS_ADMIN：系统管理员，可访问 `/admin/*`。

权限摘要：
- `/admin/*`：仅 SYS_ADMIN。
- `/keys/*`：必须使用 JWT 或有效 `X-API-Key`，仅限 INDIVIDUAL/TEAM_ADMIN/SYS_ADMIN。
- `/detect`：必须使用 JWT 或有效 `X-API-Key`（二选一，任选其一即可）。

## 目录结构

```
backend/
  app/
    main.py              # 入口
    core/                # 配置、安全、日志
    db/                  # 数据库初始化与会话
    models/              # ORM 模型（预留）
    schemas/             # Pydantic 模型（预留）
    api/v1/              # 路由（当前含健康检查）
    services/            # 业务逻辑（预留）
backend/Dockerfile       # 后端镜像构建
backend/requirements.txt # 依赖
.env.example             # 环境变量示例
```

## 快速开始（Docker Compose）

1. 复制环境变量示例：
   ```bash
   cp .env.example .env
   ```
   建议修改 `.env` 中的 `SECRET_KEY` 为自定义的强随机值。
2. 启动服务（需要 Docker 与 Docker Compose）：
   ```bash
   docker compose up --build
   ```
3. 运行数据库迁移（首次启动或模型更新后执行）：
   ```bash
   docker compose exec api alembic upgrade head
   ```
4. 访问健康检查与文档：
   - 健康检查：http://localhost:8000/health
   - OpenAPI 文档：http://localhost:8000/docs

PostgreSQL 数据使用 `postgres_data` 卷持久化。

## 本地开发（可选）

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt

# 开发模式热重载
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## 生产部署建议

### 使用 Uvicorn（轻量场景）
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
```

### 使用 Gunicorn + UvicornWorkers（推荐）
```bash
gunicorn -k uvicorn.workers.UvicornWorker app.main:app \
  --bind 0.0.0.0:8000 \
  --workers 4 \
  --access-logfile -
```

## 本地验证（Lint + Test）

在项目根目录下：
```bash
cd backend
ruff check app tests
pytest
```

## PR 验收步骤

1. 查看 GitHub Actions：确认 CI 工作流 `CI / lint-and-test` 在本分支为绿色。
2. 本地验证（可选）：在项目根目录执行 `cd backend && ruff check app tests && pytest`，确保无错误。
3. 运行数据库迁移：`docker compose exec api alembic upgrade head`（确保新字段/索引已到位）。
4. 端到端快速验证（任选其三条 curl 验证即可）：
   - `curl -i http://localhost:8000/health`
   - `curl -i http://localhost:8000/db/ping`
   - `curl -i -X POST http://localhost:8000/auth/register -H "Content-Type: application/json" -d '{"email":"pr-check@example.com","password":"StrongPass!23"}'`
   - `curl -i -X POST http://localhost:8000/auth/login -H "Content-Type: application/json" -d '{"email":"pr-check@example.com","password":"StrongPass!23"}'`
   - `curl -i -X POST http://localhost:8000/detect -H "Content-Type: application/json" -d '{"text":"hello world"}'`

## 验收与自测

以下命令在 Docker Compose 启动并完成迁移后执行：

```bash
# 1) 健康检查
curl -i http://localhost:8000/health

# 2) 数据库连通性检查
curl -i http://localhost:8000/db/ping

# 3) 查看根路由欢迎信息
curl -i http://localhost:8000/

# 4) 运行数据库迁移（确保字段/索引创建）
docker compose exec api alembic upgrade head

# 5) 查看迁移历史（可选）
docker compose exec api alembic history --verbose

# 6) 认证流程（注册 → 登录 → 获取当前用户）
curl -i -X POST http://localhost:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email": "tester@example.com", "password": "StrongPass!23"}'

curl -i -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "tester@example.com", "password": "StrongPass!23"}'

TOKEN=$(curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "tester@example.com", "password": "StrongPass!23"}' | jq -r '.access_token')

curl -i http://localhost:8000/auth/me -H "Authorization: Bearer ${TOKEN}"

# 7) 创建 API Key（仅返回一次明文 key）
API_KEY=$(curl -s -X POST http://localhost:8000/keys \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"name": "CI self-test"}' | jq -r '.key')

# 8) 使用 JWT 列出当前 Keys（示例）
curl -i http://localhost:8000/keys -H "Authorization: Bearer ${TOKEN}"

# 9) 使用新创建的 API Key 自检（验收必需）
curl -i http://localhost:8000/keys/self-test -H "X-API-Key: ${API_KEY}"

# 10) 使用 JWT 进行检测（会落库 detections 表；/detect 支持 Bearer 或 X-API-Key）
curl -i -X POST http://localhost:8000/detect \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"text": "This is a human written text."}'

# 11) 使用 API Key 进行检测（等价于 JWT）
curl -i -X POST http://localhost:8000/detect \
  -H "X-API-Key: ${API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"text": "aaaa aaaa aaaa", "options": {"language": "en"}}'

# 12) 查询检测记录，分页 + 时间过滤
curl -i "http://localhost:8000/detections?page=1&page_size=5&from=2024-01-01T00:00:00Z" \
  -H "Authorization: Bearer ${TOKEN}"

# 13)（RBAC 验收）准备一个管理员用户并提升为 SYS_ADMIN
curl -i -X POST http://localhost:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email": "admin@example.com", "password": "StrongPass!23"}'
ADMIN_TOKEN=$(curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "admin@example.com", "password": "StrongPass!23"}' | jq -r '.access_token')
# 提升角色（需要数据库容器名为 db，数据库名来自 .env.example）
docker compose exec db psql -U postgres -d AIDetector -c "UPDATE users SET role='SYS_ADMIN' WHERE email='admin@example.com';"

# 14) 用普通用户的 Token 访问 /admin/status（预期 403，返回 {code,message,detail}）
curl -i http://localhost:8000/admin/status -H "Authorization: Bearer ${TOKEN}"

# 15) 用 SYS_ADMIN Token 访问 /admin/status（预期 200）
curl -i http://localhost:8000/admin/status -H "Authorization: Bearer ${ADMIN_TOKEN}"

# 16) 团队：创建团队（创建者自动成为 OWNER）
TEAM_ID=$(curl -s -X POST http://localhost:8000/teams \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"name": "demo-team"}' | jq -r '.id')

# 17) 团队：添加成员（仅 OWNER/ADMIN 可操作）
curl -i -X POST http://localhost:8000/teams/${TEAM_ID}/members \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"user_id": 2, "role": "MEMBER"}'  # 将 user_id 替换为实际用户 ID

# 18) 团队：查看按天聚合的检测统计（仅团队成员可访问）
curl -i "http://localhost:8000/teams/${TEAM_ID}/stats?start=2024-01-01T00:00:00Z&end=2024-12-31T00:00:00Z" \
  -H "Authorization: Bearer ${TOKEN}"
```

预期：
- `/health` 返回 `{ "status": "ok" }` 且状态码 200。
- `/db/ping` 返回 `{ "status": "ok" }` 且状态码 200，重复启动不会丢数据。
- `/docs` 页面可正常打开。
- `/admin/status` 普通用户 403，SYS_ADMIN 200。
- 异常返回统一格式 `{code, message, detail}`，其中 `detail` 会根据场景给出具体信息（表单校验、权限不足等）。
