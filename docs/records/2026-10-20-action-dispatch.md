# action 分流本地验收（2026-10-20）

状态：本机验收完成。本记录保存提交前的测试证据；后续代码推送与审查状态以关联 PR 为准，不表示服务已部署或生产 PHP 透传已验收。

最新合并前复核结果：后端 `591 passed, 10 skipped`、定向 `114 passed`；检测端 `75 tests OK`。下文初次验收的 575/98/73 为历史基线，异步审查修复与复验见末节。

## 变更

- 后端主检测与就绪探针继续发送 `{"text":"文本"}`；Evidence 复用生效检测地址，发送 `{"text":"完整原文","action":"evidence"}`，不再派生 `/evidence/route`，不新增 URL 配置。
- 检测端 `/detect` 默认或 `action=detect` 走主检测，`action=evidence` 直接复用现有 Evidence 处理；严格请求模型、脱敏输入错误、独立容量和超时保留。旧 `/evidence/route` 继续可用。
- 生产取消 `.php` 后缀禁令；HTTPS、令牌及其他生产约束保留。Bundle、参考算法、主检测模型、数据库结构与现有 `.env` 未修改。
- 更新两端运行文档、后端配置模板注释、部署说明和未发布变更记录。建议先更新检测端，再更新后端。

## 本地检查与回归

- 后端：`575 passed, 10 skipped`，全量 pytest 正常退出。定向配置、客户端和 Evidence HTTP 测试为 `98 passed`，含 PHP 丢 action/裁剪响应时的降级、整篇一次、分段主检测、幂等重放、配额与落库回归。
- 10 个跳过均为显式 opt-in：PostgreSQL 幂等并发 3 个、PostgreSQL 历史认领 4 个、独立真实模型验收入口 1 个、两个外部 Bundle smoke 各 1 个。本轮未运行 PostgreSQL 并发专项；当前短文 Bundle 与真实模型另经下述联调验证。
- 检测端：`73 tests OK`，无失败、无跳过；覆盖 action 默认/显式主检测、Evidence 严格校验及原接口、鉴权、繁忙、超时、取消和占位释放。
- 后端全目录 `ruff check app tests`、检测端改动文件 Ruff、检测端编译检查、两产品仓库 `git diff --check` 通过。
- 检测端改动文件及后端 `test_evidence_http.py` 的 Ruff 格式检查通过；后端其余四个改动 Python 文件在 HEAD 已有格式差异，沿用既有格式，未扩大为全文件格式化。两处测试文件整理前后 AST 一致。
- Windows 沙箱曾阻塞 asyncio 创建回环 socketpair；经自动审批后以同一 Python 在沙箱外重跑通过。pytest 使用工作区专属临时目录，避免默认用户临时目录 ACL 问题。未改环境或安装依赖。

后端全量命令（在 `backend/` 执行，重跑时改用新的临时目录）：

```powershell
& D:/Anaconda/envs/lab/python.exe -m pytest -q --basetemp=D:/Code/.tmp/action-backend-full-20261020 -o faulthandler_timeout=60 -p no:cacheprovider --tb=short
```

检测端全量命令（在检测端仓库根执行）：

```powershell
& D:/Anaconda/envs/lab/python.exe -u -m unittest discover -s tests -v
```

## 真实模型联调

使用现有 CUDA 主模型、CPU Router 与冻结短文 Bundle；不安装依赖、不下载、不重训、不修改 `.env`，使用进程临时令牌和实际 ASGI 应用，不发送生产请求。固定 PHP 转发由进程内模拟器实现，因此不是生产 PHP 验收。

| 场景 | 结果 |
| --- | --- |
| 后端客户端直连 `/detect` + `action=evidence` | 原文保持，Router 1 次、主模型 0 次，22 个指标，`partial` |
| 同一客户端经过模拟固定 PHP `/detect` 转发 | 原文保持，Router 1 次、主模型 0 次，22 个指标，`partial` |
| 旧 `/evidence/route` | 返回 `routed`，通过后端严格 Schema/SHA 校验 |
| 默认与显式主检测、短文本就绪探针 | 正常；前后主检测结果一致 |
| 模拟 PHP 丢失 action | 严格识别为 `invalid_router_response`，Evidence 降级，不重试 |
| 模拟 PHP 将结果替换为主检测 JSON | 严格识别为 `invalid_router_response`，Evidence 降级，不重试 |

样本为保留首尾空白和 CRLF 的 277 字符中文整文；Router 输出 `zh/webtext`。`partial` 表示部分参考比较可用，非路由失败。本次是协议与计算链联调，不是路由准确率、生产性能或并发容量评估。

- Router SHA：`9a1a5be8f0d7599e682d30c7f50e3deb5f747b59ef720e288f6342e7c944f896`。
- Bundle SHA：`f04fee602a09850adae31e5e78ea9926a27cd96663aeeeaeaafb11d8a959fb0e`。
- Python 环境：`D:/Anaconda/envs/lab/python.exe`，Torch `2.9.1+cu128`、Transformers `4.53.0`、FastAPI `0.128.0`、Pydantic `2.12.5`、NumPy `2.2.5`、py3langid `0.4.0`；不是目标 Linux 环境或 requirements 全新安装验收。
- 本次联调脚本与原始结果保留于工作区 `D:/Code/.tmp/action-split-20261020/verify_real_models.py` 和 `real-model-results.json`；脚本拒绝覆盖已有结果，重跑需换目录。
- 后端基线 HEAD：`5e39866cc00da018ccdd9f9e808790c5d3b75b00`；检测端基线 HEAD：`b882bd1e8e9d92c42175671361a1de2c99c459f3`。

## 仍需服务器确认

PHP 必须完整转发 JSON 请求中的 `action`、服务令牌，以及上游完整 JSON 响应与状态码。更新检测端后，经真实 PHP 入口测试 Evidence，检查 `status=routed`、五字段齐全且 SHA 匹配；HTTP 200 或普通检测分数不算通过。

验收阶段未修改 PHP、未触碰服务器或执行部署。Docker Desktop 引擎当时未启动；本机测试通过 Python/ASGI 和隔离 SQLite 完成，没有启动或重建 Docker 服务。代码提交和推送由后续独立操作完成，不改变上述验收边界。

## 合并前异步审查修复与复验

将两 PR 转为正式后，Codex 对初始提交各发现一个有效 P2，已复现并修复：

- 后端：配置认可的 `.PHP`/`.PhP` 地址被 Evidence 大小写检查拒绝，且将同类地址用于 base URL 时可能被追加 `/detect`。共享入口识别与 Evidence 后缀判断统一转小写判断，实际发送 URL 保留大小写。
- 检测端：顶层数组/标量或错误 Content-Type 的原始字节没有解析成对象，导致请求校验退回默认处理器并回显原文。现在 `/detect` 所有请求模型校验错误均为固定脱敏对象；已识别 Evidence 保留原错误码，其他为 `INVALID_DETECT_REQUEST`。正常主检测、空白文本业务检查、模型错误、认证及容量不变。

检测端新增测试先复现 6 个回显子用例，修复后完整 75 项全通过、零跳过；后端新增 16 项大小写与可信边界覆盖，最终全量 591 通过、10 跳过，定向 114 通过。Ruff、编译、diff 检查及独立只读复核通过。

后端最终全量命令（同样使用工作区专属临时目录）：

```powershell
& D:/Anaconda/envs/lab/python.exe -m pytest -q -rs --basetemp=D:/Code/.tmp/action-backend-case-full-20261020 -p no:cacheprovider --tb=short -o faulthandler_timeout=60
```

真实模型再次加载并完成 8 组联调检查：原有直连、模拟固定 PHP、旧接口与就绪探针、主结果不变和异常转发降级均通过；新增混合大小写 PHP 入口和非对象/Content-Type 错误脱敏也通过，非法输入未运行任一模型。原文、SHA、22 项观察和 `partial` 结果保持；实际 URL 大小写未改写。

复验脚本与结果位于 `D:/Code/.tmp/action-split-review-fix-20261020/verify_real_models.py` 和 `real-model-results.json`。这是同一组现有 CUDA/CPU 模型及 Bundle 的本机 ASGI 验证，生产 PHP、目标 Linux 环境和 PostgreSQL 并发专项仍未验收。最终云端 CI 与 Codex 审查状态以 PR #8/#10 的最新 head 为准。
