# AIDetector Backend

本项目基于 **FastAPI + PostgreSQL + SQLAlchemy 2.0 + Alembic + JWT + bcrypt** 构建，当前实现最小可运行后端与健康检查。

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
   - 数据库连通性：http://localhost:8000/db/ping
   - OpenAPI 文档：http://localhost:8000/docs

日志默认过滤敏感字段（password/secret/token/api_key），请勿在日志中主动输出明文秘密。

PostgreSQL 数据使用 `postgres_data` 卷持久化。

## 本地开发（可选）

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
uvicorn app.main:app --reload
```

## 验收与自测

以下命令在 Docker Compose 启动并完成迁移后执行：

```bash
# 1) 健康检查
curl -i http://localhost:8000/health

# 2) 根路由欢迎信息
curl -i http://localhost:8000/

# 3) 数据库连通性检查（需先执行 alembic upgrade head）
curl -i http://localhost:8000/db/ping

# 4) 获取 OpenAPI JSON（docs 页面的后端数据）
curl -i http://localhost:8000/openapi.json

# 5) 数据库迁移状态（可选）
docker compose exec api alembic history --verbose

# 6) 错误格式示例（访问不存在路由）
curl -i http://localhost:8000/not-found
```

预期：
- `/health` 返回 `{ "status": "ok" }` 且状态码 200。
- `/db/ping` 返回 `{ "status": "ok" }` 且状态码 200。
- `/docs` 页面可正常打开。
- 错误路径返回形如 `{ "code": 404, "message": "Not Found", "detail": "Not Found" }`。
