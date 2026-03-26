# AIDetector Backend

这个 README 只解决两件事：

1. 后端怎么在服务器上启动和更新
2. 当前检测链路到底是怎么工作的

前端仓库在同级目录 `AIDetector-Web`。如果你后面要改前端展示，优先同时看这两个文件：

- `backend/app/api/v1/detections.py`
- `docs/detection-contract.md`

## 1. 目录约定

下面命令默认你已经进入后端仓库根目录：

```bash
cd /srv/aidetector/AIDetector-Back
```

路径不一定非得一样，但后面的命令都默认当前目录就是这个仓库。

## 2. 首次部署前要改什么

先复制环境变量：

```bash
cp .env.example .env
```

编辑 `.env`：

```bash
nano .env
```

至少要确认这些值：

```env
ENVIRONMENT=production
SECRET_KEY=请替换成至少32位随机字符串
POSTGRES_PASSWORD=请替换成强密码
BACKEND_CORS_ORIGINS=https://你的域名

# 兼容旧配置，保留也没问题
DETECT_SERVICE_URL=https://umcat.cis.um.edu.mo

# 正式检测地址
DETECT_SERVICE_DETECT_URL=https://umcat.cis.um.edu.mo/api/aidetect.php

# 如果算法方提供独立健康检查地址，再单独配置
# DETECT_SERVICE_HEALTH_URL=https://umcat.cis.um.edu.mo/api/health.php
```

注意：

1. 生产环境必须是 `ENVIRONMENT=production`
2. `SECRET_KEY` 和数据库密码必须换掉
3. Linux 生产环境不要继续用 `host.docker.internal`
4. 如果没配 `DETECT_SERVICE_HEALTH_URL`，`/api/v1/ready` 会直接用 detect 接口做一次极短 probe

## 3. 当前检测行为

这部分是当前线上约定，前后端都已经按这个实现：

1. 下游算法只返回 `score / threshold / label / model_name`
2. 后端按公式把原始分转换成展示概率：

```text
prob_ai = 1 / (1 + exp(-(raw_score - threshold)))
```

3. 输入文本按换行切段
4. 少于 `200` 个非空白字符的相邻段落会自动合并后再送检
5. 整体 AI 概率按“检测块非空白字符数”做加权平均
6. 前端预览区始终保持原始段落结构，不会因为合并检测而把多段压成一段
7. 对用户显示的模型版本固定为 `v1.0`
8. 真实上游模型名不直接对用户展示，内部保存在 `provider_model_name`
9. 当前用户主路径只展示 `AI / Human` 两类

更完整的字段语义、替换点和联动点看：

- [docs/detection-contract.md](./docs/detection-contract.md)

## 4. 一键脚本

仓库里有 3 个脚本：

```bash
bash scripts/server-up.sh
bash scripts/server-update.sh
bash scripts/server-check.sh
```

### `server-up.sh`

用途：

- 首次启动
- 日常重启
- 重建并拉起服务
- 自动执行迁移
- 自动健康检查

### `server-update.sh`

用途：

- 服务器上更新代码
- `git pull --ff-only`
- 重建后端服务
- 自动执行迁移
- 自动健康检查

### `server-check.sh`

用途：

- 检查容器状态
- 检查 `/api/v1/health`
- 检查 `/api/v1/ready`

## 5. 首次启动

先给脚本执行权限：

```bash
chmod +x scripts/*.sh
```

然后一键启动：

```bash
bash scripts/server-up.sh
```

这个脚本会自动做：

1. 检查 `.env`
2. `docker compose up -d --build`
3. `alembic upgrade head`
4. 健康检查

## 6. 日常更新

服务器上更新版本：

```bash
bash scripts/server-update.sh
```

这个脚本会自动做：

1. `git pull --ff-only`
2. 重建并重启容器
3. 执行迁移
4. 做健康检查

## 7. 健康检查

任何时候想确认服务是不是活着：

```bash
bash scripts/server-check.sh
```

它会检查：

- `docker compose ps`
- `/api/v1/health`
- `/api/v1/ready`

如果 `/ready` 不是 `ok`，不要继续切前端流量。

## 8. 手动命令

不走脚本时可以手动执行：

### 启动服务

```bash
docker compose up -d --build
```

### 执行迁移

```bash
docker compose exec api alembic upgrade head
```

### 查看容器状态

```bash
docker compose ps
```

### 查看日志

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

## 9. 管理员账号

如果需要后台管理员，先注册一个普通账号，再进数据库提权：

```bash
docker compose exec db psql -U postgres -d aidetector
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

## 10. 常见问题

### 10.1 `.env not found`

```bash
cp .env.example .env
nano .env
```

### 10.2 `/ready` 不通过

优先检查：

- 数据库容器是否正常
- `DETECT_SERVICE_DETECT_URL` 或 `DETECT_SERVICE_URL` 是否可达
- 如果没配 `DETECT_SERVICE_HEALTH_URL`，确认下游算法接口允许极短 probe 文本

然后看日志：

```bash
docker compose logs -f api
docker compose logs -f db
```

### 10.3 前端请求报 401 / 登录异常

优先查：

- 前后端是否同域
- Nginx `/api` 反代是否正确
- HTTPS 是否配置完整
- `BACKEND_CORS_ORIGINS` 是否写对

### 10.4 检测结果看起来和段落不一致

当前规则是：

- 原文按换行切段
- 不足 `200` 个非空白字符的相邻段落会自动合并检测
- 右侧结果卡片会显示 `段落 X-Y · 合并检测`
- 左侧预览区保持原始段落结构，相同颜色表示共享同一检测块

如果要改这个规则，不要只改一处，直接看：

- `docs/detection-contract.md`
