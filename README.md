# AIDetector Backend V1.0

`AIDetector-Back` 是 AIDetector V1.0 的后端服务，当前正式对外提供：

- 用户注册 / 登录 / 游客 token
- 文本检测
- 配额管理
- 历史记录
- PDF 报告
- API Key
- 管理后台接口
- 团队接口
- 健康检查与就绪检查

当前仓库根目录只保留这套文档结构：

- [`README.md`](./README.md)：V1.0 总说明
- [`contract/openapi.yaml`](./contract/openapi.yaml)：正式契约
- [`contract/changelog.md`](./contract/changelog.md)：契约版本变更
- [`docs/detection-contract.md`](./docs/detection-contract.md)：检测语义说明
- [`docs/deploy-cutover-checklist.md`](./docs/deploy-cutover-checklist.md)：上线切换清单

执行代码 

```bash
docker compose up -d --build
```


## V1.0 边界

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
- 不代表这些能力已经进入 V1.0 正式产品面

## 运行结构

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
DETECT_SERVICE_DETECT_URL=https://umcat.cis.um.edu.mo/api/aidetect.php
```

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

推荐直接用脚本：

```bash
bash scripts/server-up.sh
```

脚本顺序：

1. 校验 `.env` 和 `.env.ops`
2. 启动 `db`
3. 等数据库 ready
4. 确保 `.env` 里的运行账号存在并完成授权
5. 构建 API 镜像
6. 用管理员账号跑迁移
7. 启动 `api`
8. 跑 `health / ready`

常用检查：

```bash
docker compose ps
docker compose logs -f db
docker compose logs -f api
curl http://127.0.0.1:8000/api/v1/health
curl http://127.0.0.1:8000/api/v1/ready
```

## V1.0 生产部署

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
DETECT_SERVICE_DETECT_URL=https://你的检测服务地址
```

`.env.ops`

```env
POSTGRES_USER=postgres
POSTGRES_PASSWORD=强管理员密码
POSTGRES_DB=AIDetector
```

### 2. 端口建议

当前仓库默认开发端口映射是 `8000:8000`。  
如果你按推荐结构部署，生产建议改成：

```yml
ports:
  - "127.0.0.1:8020:8000"
```

然后由 Nginx / Caddy 转发 `/api` 到 `127.0.0.1:8020`。

### 3. 启动

推荐：

```bash
bash scripts/server-up.sh
```

更新代码后：

```bash
bash scripts/server-update.sh
```

### 4. 必须记住

- 生产目录不要带 `docker-compose.override.yml`
- 数据库不要暴露宿主机端口
- 后端不要直接把 `8000/8020` 开给公网
- 反代层统一处理 `/api`
- `.env` 只放运行账号
- `.env.ops` 只放管理员账号

## 手工维护

### 用管理员账号进库

```bash
docker compose exec db psql -U postgres -d AIDetector
```

### 手工跑管理员迁移

```bash
set -a
source .env.ops
set +a

docker compose run --rm --no-deps \
  -e POSTGRES_USER="$POSTGRES_USER" \
  -e POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
  api alembic upgrade head
```

## V1.0 验收清单

- `docker compose ps`
- `/api/v1/health`
- `/api/v1/ready`
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
