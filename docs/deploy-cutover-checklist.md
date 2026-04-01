# 上线切换清单

这份清单只处理当前真实生效的生产切换流程。

## 0. 目标状态

上线完成后应该满足：

- 生产目录没有 `docker-compose.override.yml`
- 数据库没有宿主机端口映射
- `postgres` 已改成强密码
- `aidetector_app` 已改成强密码
- API 长期运行在 `aidetector_app`
- `.env` 只存运行账号
- `.env.ops` 只存管理员账号
- `/api/v1/health` 正常
- `/api/v1/ready` 正常

## 1. 先检查生产目录

生产目录里不应该存在：

- `docker-compose.override.yml`

先确认：

```bash
ls -la
```

## 2. 准备两套密码

至少准备两套不同的强密码：

- `postgres`
- `aidetector_app`

不要复用。

## 3. 先改管理员账号 `postgres`

```bash
docker compose exec db psql -U postgres -d AIDetector
```

```sql
ALTER ROLE postgres WITH PASSWORD '你的强管理员密码';
```

## 4. 再改业务账号 `aidetector_app`

```sql
ALTER ROLE aidetector_app WITH PASSWORD '你的强业务密码';
```

如果线上还没有这个账号，先创建并授权：

```sql
CREATE ROLE aidetector_app LOGIN PASSWORD '你的强业务密码';
GRANT CONNECT ON DATABASE "AIDetector" TO aidetector_app;
\connect "AIDetector"
GRANT USAGE ON SCHEMA public TO aidetector_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO aidetector_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO aidetector_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO aidetector_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO aidetector_app;
```

## 5. 准备运行时 `.env`

```bash
cp .env.example .env
```

至少写这些：

```env
ENVIRONMENT=production
SECRET_KEY=你的强随机串
POSTGRES_HOST=db
POSTGRES_PORT=5432
POSTGRES_USER=aidetector_app
POSTGRES_PASSWORD=你的强业务密码
POSTGRES_DB=AIDetector
BACKEND_CORS_ORIGINS=https://你的域名
DETECT_SERVICE_DETECT_URL=https://你的检测服务地址
```

## 6. 准备运维 `.env.ops`

```bash
cp .env.ops.example .env.ops
```

至少写这些：

```env
POSTGRES_USER=postgres
POSTGRES_PASSWORD=你的强管理员密码
POSTGRES_DB=AIDetector
```

## 7. 启动和迁移

推荐直接跑：

```bash
bash scripts/server-up.sh
```

更新代码后跑：

```bash
bash scripts/server-update.sh
```

脚本会自动：

- 读 `.env`
- 读 `.env.ops`
- 起 `db`
- 确保运行账号存在
- 用管理员账号跑迁移
- 再起 `api`

## 8. 检查端口暴露

```bash
docker compose ps
```

你应该看到：

```text
api ... 0.0.0.0:8000->8000/tcp
db  ... 5432/tcp
```

你不应该看到：

```text
db ... 0.0.0.0:15432->5432/tcp
```

也不应该看到：

```text
db ... 127.0.0.1:15432->5432/tcp
```

## 9. 健康检查

```bash
curl http://127.0.0.1:8000/api/v1/health
curl http://127.0.0.1:8000/api/v1/ready
```

`/ready` 必须确认数据库和 detect service 都是 `ok`。

## 10. 验证应用确实跑在 `aidetector_app`

```bash
docker compose exec db psql -U postgres -d AIDetector
```

```sql
SELECT usename, datname, client_addr, application_name
FROM pg_stat_activity
WHERE datname = 'AIDetector';
```

你应该能看到应用连接使用的是 `aidetector_app`。

## 11. 最后再过一遍

- `postgres` 已强密码
- `aidetector_app` 已强密码
- `.env` 使用 `aidetector_app`
- `.env.ops` 保存 `postgres`
- 生产目录没有 `docker-compose.override.yml`
- `docker compose ps` 没有 DB 宿主机端口映射
- 云安全组没有开放 `5432` / `15432`
- `/api/v1/health` 正常
- `/api/v1/ready` 正常
