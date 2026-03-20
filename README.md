# AIDetector Backend

这是后端仓库的快捷部署说明。  
如果你要看整套 `AIDetector V1.0` 的完整上线文档，请看前端仓库根目录的 [README.md](/D:/Code/AIDetector-web/README.md)。

这个 README 只解决一件事：

- 让后端在服务器上**尽可能一键启用**

---

## 1. 目录约定

后端目录假设是：

```txt
/srv/aidetector/AIDetector-Back
```

你可以不是这个路径，但后面的命令都默认你已经 `cd` 到后端仓库根目录。

---

## 2. 首次部署前要改什么

先复制环境变量：

```bash
cp .env.example .env
```

然后编辑：

```bash
nano .env
```

至少改这些值：

```env
ENVIRONMENT=production
SECRET_KEY=请替换成至少32位随机字符串
POSTGRES_PASSWORD=请替换成强密码
BACKEND_CORS_ORIGINS=https://你的域名
DETECT_SERVICE_URL=http://你的RepreGuard地址:9000
```

注意：

1. `ENVIRONMENT` 上线必须是 `production`
2. `SECRET_KEY` 不能短，也不能继续用默认占位值
3. `POSTGRES_PASSWORD` 必须改
4. `DETECT_SERVICE_URL` 不要在 Linux 生产环境继续用 `host.docker.internal`

如果这些值没改对，后端在 `production` 下会直接拒绝启动。

---

## 3. 一键脚本

这个仓库现在提供 3 个脚本：

```bash
bash scripts/server-up.sh
bash scripts/server-update.sh
bash scripts/server-check.sh
```

### `server-up.sh`

用途：

- 首次启动
- 日常重启
- 重新构建并拉起服务
- 自动跑迁移
- 自动做健康检查

### `server-update.sh`

用途：

- 服务器上更新代码
- `git pull`
- 重建后端服务
- 自动跑迁移
- 自动做健康检查

### `server-check.sh`

用途：

- 检查容器状态
- 检查 `/api/v1/health`
- 检查 `/api/v1/ready`

---

## 4. 首次启动

### 4.1 给脚本执行权限

Linux 服务器第一次执行前：

```bash
chmod +x scripts/*.sh
```

### 4.2 一键启动

```bash
bash scripts/server-up.sh
```

这个脚本会自动做：

1. 检查 `.env`
2. `docker compose up -d --build`
3. `alembic upgrade head`
4. 健康检查

---

## 5. 日常更新

服务器上要更新版本时：

```bash
bash scripts/server-update.sh
```

这个脚本会自动做：

1. `git pull --ff-only`
2. 重建并重启容器
3. 执行迁移
4. 做健康检查

---

## 6. 健康检查

任何时候你想确认后端是不是活着：

```bash
bash scripts/server-check.sh
```

这个脚本会输出：

- `docker compose ps`
- `/api/v1/health`
- `/api/v1/ready`

只要 `/ready` 不是 `ok`，就不要继续接前端流量。

---

## 7. 不走脚本时的手动命令

如果你想手动做，也可以直接跑：

### 启动服务

```bash
docker compose up -d --build
```

### 跑迁移

```bash
docker compose exec api alembic upgrade head
```

### 查看容器状态

```bash
docker compose ps
```

### 看日志

```bash
docker compose logs -f api
docker compose logs -f db
```

### 存活检查

```bash
curl http://127.0.0.1:8000/api/v1/health
```

### 就绪检查

```bash
curl http://127.0.0.1:8000/api/v1/ready
```

---

## 8. 管理员账号

如果你需要管理员后台，先注册一个普通账号，然后进入数据库提升角色：

```bash
docker compose exec db psql -U postgres -d AIDetector
```

执行：

```sql
UPDATE users
SET role = 'SYS_ADMIN'
WHERE email = '你的管理员邮箱';
```

退出：

```sql
\q
```

---

## 9. 常见问题

### 9.1 脚本报 `.env not found`

先执行：

```bash
cp .env.example .env
nano .env
```

### 9.2 `/ready` 不通过

优先检查：

- 数据库容器是否正常
- `DETECT_SERVICE_URL` 是否可达

然后看日志：

```bash
docker compose logs -f api
docker compose logs -f db
```

### 9.3 服务起来了，但前端调用 401 / 登录异常

大概率不是后端容器没起来，而是：

- 前端和后端没同域
- Nginx `/api` 没反代好
- HTTPS 没配置好
- `BACKEND_CORS_ORIGINS` 没写对

这部分看主 README。
