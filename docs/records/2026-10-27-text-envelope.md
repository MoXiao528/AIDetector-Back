# text 封装演示协议：本地实施与验收

日期：2026-10-27。用户批准只在后端与检测端各自的 `demo/text-envelope` 实施，不提交、推送、合并或部署。

## 范围与基线

- 后端从干净 main `40d041f287b7e5ae304e0e6894e7ae158338aefc` 创建演示分支。
- 检测端从干净 main `195843ae19e02877936ddd2150828cc9548aabc2` 创建同名分支。
- 两个 main 引用未移动。前端 `feat/evidence-display-switch` 的既有修改、研究 `research/writing-metrics-audit` 的离线工作保留；研究仅在现有台账追加本项记录。
- 不修改实际 `.env`、Compose、模型、Router、Bundle、数据库或前端。前端当前本机 `.env.production` 是 true，与旧台账的 false 记录不同；不据此推断线上 dist 已恢复。

## 最终实现

- 后端 `DETECT_TEXT_ENVELOPE_ENABLED=false` 默认保持原 JSON。开启时发送 `{"text":"AIDT:1:detect\n原文"}` 或 `{"text":"AIDT:1:evidence\n完整原文"}`；不使用 Base64/HMAC。
- 主检测、Evidence 和主检测式就绪探针共用发送逻辑。两种模式检查原文严格字符串、非空白、1～20,000 字符；构造实际 HTTP 请求后检查 128 KiB，再发送同一份请求。非法 UTF-8 序列化错误脱敏，请求体超限不进入主检测长度重试。
- 检测端只在 `/detect` union 模型校验前解一层，先于原文 20,000 字符限制。服务 Token、实际流式 128 KiB 限制、旧 `/evidence/route`、模型预处理、并发和超时保留。
- `AIDT:` 是保留命名空间，封装禁止额外外层字段。开启封装后正文中的 JSON、换行或嵌套协议头不再解释；旧协议裸发此前缀正文存在兼容例外。
- 无 HMAC 不保证发现所有仍然合法的正文截短/改写。PHP 字符串处理、响应裁剪与丢 action 是不同问题，不能称已一并解决。

## 验证

| 检查 | 结果 |
|---|---|
| 检测端完整 unittest | 88 通过，0 跳过 |
| 后端完整 pytest | 695 通过，10 项显式 opt-in 跳过，279.24 秒，退出码 0 |
| 两端 Ruff / diff 检查 | 最终文件集已通过 |
| 跨仓实际客户端 → 模拟 PHP → 实际 ASGI | 6 组通过，模型调用使用桩 |
| 现有真实主模型、Router、冻结 Bundle | 3 组通过：旧协议透传、封装且仅留 text（UTF-8）、封装且仅留 text（ASCII JSON 重编码） |
| main Settings 回切检查 | 实际 main 源码拒绝残留的新 dotenv 键（即使 false）；移除后恢复加载 |
| 独立 OpenAPI validator | 未运行：本机缺模块，临时目录安装尝试因软件源域名解析失败未成功；OpenAPI 文件未修改 |
| 真实生产 PHP / 目标 Linux / 部署回退演练 | 未执行 |

10 个跳过项为 PostgreSQL 幂等 3 项、claim 4 项、两种真实 Bundle smoke 各 1 项、完整真实模型业务链 opt-in 1 项；本轮另行执行下述现有真实模型跨仓验证，不将其冒充完整业务链测试。后端原 591 项加本轮 104 项，共 695 项通过，未删除既有覆盖。

跨仓验证使用实际后端客户端、实际检测端 FastAPI 路由和 ingress；模拟转发器只保留 text，未连接生产 PHP。主响应检查 probability、有限范围和阈值标签；Evidence 检查五字段、合法 route 和 Bundle 绑定 SHA。每组主请求仅调用主模型一次，Evidence 请求 Router 一次、主模型零次，整文逐字符保留。主结果在旧/新协议间一致。

真实模型复验使用已有 CUDA 主模型、CPU Router 和原冻结短文 Bundle，3 组仍产生 22 项观察和 partial 参考比较；这是传输与计算链验收，不是准确率、吞吐或部署认证。测试进程开启离线模式，不下载、训练或替换模型，结束后排空并退出。

额外失败对照覆盖关闭封装时 PHP 丢 action、响应裁剪、协议头截坏及大量 emoji 被 PHP ASCII 转义后超限；Evidence 按原合同降级。普通 JSON、前缀正文、中文/emoji 的 20,000 字符边界、原文空白和引号、128 KiB 实际字节边界、发送前拒绝及历史/幂等/配额另由回归测试覆盖。

### 环境与失败尝试

- Windows 受限环境的 asyncio 创建事件循环时卡在本地 socket pair，未进入业务代码；诊断后停止该进程，以允许本地回环的执行方式重跑通过，不修改应用或事件循环实现。自动权限审核曾超时，按工具提示重试获准。
- 后端首轮全量为 403 通过、10 跳过、308 fixture errors（292 setup、16 重复 teardown）；首个及重复错误为默认 `pytest-of-Administrator` 临时根的 WinError 5。403＋292＝695，错误计数差异不表示减少测试。保留原目录与失败日志，改全新工作区临时根重跑。新增长文本参数改用短测试 ID，避免日志展开整段测试字符串。
- 本机 lab：FastAPI 0.128.0、Pydantic 2.12.5、Starlette 0.50.0、HTTPX 0.28.1、Torch 2.9.1+cu128、Transformers 4.53.0、Ruff 0.15.0。仓库 requirements 锁定的部分版本不同，未安装/替换运行依赖；本机通过不替代目标锁定环境验收。
- 过程证据位于工作区 `.tmp/text-envelope-20261027/`：`verify_cross_service.py`、`cross-service-results.json`、`real-model-results.json`、`verify_main_config_rollback.py`、`main-config-rollback-results.json`、后端测试日志和既有文件校验快照。临时脚本仅用于本工作区复核，不是部署入口。

## 后续验收与恢复

按 [协议、部署和恢复说明](../detection-contract.md#text-封装协议演示分支)，先更新兼容检测端，再开启后端封装并重建 API。未来获得部署授权、配置好现有 Token 和 Bundle 后，在后端仓库根目录执行：

```bash
docker compose exec -T api python smoke_text_envelope.py --url https://umcat.cis.um.edu.mo/api/aidetect.php
```

脚本显式使用封装，只有主检测 probability 和 Evidence routed/五字段/SHA 均通过才输出 `PASS TEXT_ENVELOPE`；配置/响应失败输出固定错误码并非零退出。**该生产命令本轮没有执行，不能标记真实 PHP 已通过。**

回退先关后端封装并重建 API；PHP 未修好时 Evidence 同时保持 off。回 main 前删除新增 dotenv 键和环境覆盖、恢复实际生产 Compose 配置，再重建镜像/重建 API；兼容检测端可以保留。前端恢复展示需确认 `VITE_EVIDENCE_ENABLED=true` 并重新构建部署 dist，不能覆盖本机已有工作。
