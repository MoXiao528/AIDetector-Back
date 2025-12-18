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
   - OpenAPI 文档：http://localhost:8000/docs

PostgreSQL 数据使用 `postgres_data` 卷持久化。

## 本地开发（可选）

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
uvicorn app.main:app --reload
```

## 验收与自测

以下命令在 Docker Compose 启动并完成迁移后执行，用于验收：

```bash
# 1) 健康检查
curl -i http://localhost:8000/health

# 2) 数据库连通性检查
curl -i http://localhost:8000/db/ping

# 3) 根路由欢迎信息
curl -i http://localhost:8000/

# 4) 获取 OpenAPI JSON（docs 页面的后端数据）
curl -i http://localhost:8000/openapi.json

# 5) 运行数据库迁移到最新
docker compose exec api alembic upgrade head
```

预期：
- `/health` 与 `/db/ping` 均返回 `{ "status": "ok" }` 且状态码 200。
- `/` 返回欢迎消息，`/docs` 页面可正常打开。
- `alembic upgrade head` 成功执行且重复启动不会丢失数据。
