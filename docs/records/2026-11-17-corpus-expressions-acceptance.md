# 2026-11-17 中文表达本地联调验收

## 真实浏览器和 API

- Windows 原生后端使用独立 SQLite，Docker 后端使用独立 PostgreSQL。两边复用本机 `9000` 的真实检测服务。中文报道在两边均路由到 `zh/news`，命中 26 项。浏览器分别保存为原生历史 `6` 和 Docker 历史 `3`。
- 桌面验证了详情中的 `12 → 26 → 12` 切换，手机 `390×844` 验证了历史重开、刷新及换行。首检、历史和幂等回放的表达快照一致。原生 API 计数为主模型 2 个分段请求、Router 1 次，预检、历史、回放与 PDF 导出合计 0 次模型调用。中文案例结算 987 字一次。另一个真实意大利语案例正常返回主检测和 `unsupported_language`，结算 1,554 字一次。
- 原生和 Docker 均从页面下载了真实报告。两份报告各 2 页，嵌入中文字体，26 项按保存的顺序显示。实际 API 分别验证了 `includeEvidence` 两态。旧快照、未命中、无路由、长表达和 HTML 转义使用隔离历史样本补验。

[无凭据验收汇总](D:/Code/AIDetector-Back/scripts/loadtest/results/corpus-e2e-20261117-4f33d967/acceptance-summary.json) · [原生浏览器 PDF](C:/Users/Administrator/.codex/visualizations/2026/11/17/01a1e7ef-af04-7311-9a1a-9c04c2925558/real-integration/main-matched.pdf) · [Docker 浏览器 PDF](C:/Users/Administrator/.codex/visualizations/2026/11/17/01a1e7ef-af04-7311-9a1a-9c04c2925558/real-integration/docker/main-matched.pdf)

## Docker 与失败对照

- 使用仓库 `backend/Dockerfile`，复用已缓存的依赖层并重新复制当前后端。镜像 `sha256:17898b92a603c6ea681f7f5d3a8405350638f18c33bc4a67d5cd57364048b521`，Python `3.11.14`，PostgreSQL `18.1`，Alembic `20240921_0017`。
- Python 3.11 断网回归首轮 253 通过、2 跳过、2 因测试容器漏挂 OpenAPI 文件失败。两项跳过是未配置 `EVIDENCE_TEST_BUNDLE_PATH` 和 `EVIDENCE_SHORT_TEST_BUNDLE` 的独立 Bundle 冒烟测试。补挂 OpenAPI 与长文 Bundle 后定向复跑 6 项全部通过，包含长文冒烟和初轮两项失败。中文短文 Bundle 通过下述 Docker 真实 API 链路，未单跑短文冒烟用例。
- 首轮 Docker 验收误填了 65 位测试 Bundle SHA，主检测成功并保存 `invalid_evidence_config`。按文件实测 SHA 修正后重跑。同一文本两次的主分数均为 `0.9999344008309501`、标签均为 `ai`，各新增一条已完成请求并结算 987 字。修正后的 API 刻意设置旧 `DETECT_EVIDENCE_MODE=off`，仍返回正常 Evidence。浏览器历史 `3` 持久化的完整 Evidence 和独立 API 历史 `2` 的内容一致。

[Docker HTTP、回放和 PDF 回执](D:/Code/AIDetector-Back/scripts/loadtest/results/corpus-e2e-20261117-4f33d967/docker-verification/http-summary.json) · [PostgreSQL 降级对照](D:/Code/AIDetector-Back/scripts/loadtest/results/corpus-e2e-20261117-4f33d967/docker-verification/postgres-summary.json) · [浏览器记录落库](D:/Code/AIDetector-Back/scripts/loadtest/results/corpus-e2e-20261117-4f33d967/docker-verification/browser-postgres-summary.json)

## 局限与保留资源

浏览器通过本机 HTTP 入口导出时仍可复现 POST 204，具体来源未定位。独立 HTTP 客户端经同源代理 POST 返回 200 和有效 PDF。实际页面下载在临时 HTTPS 同源入口完成，系统代理、证书信任和产品代码无新增改动，本轮勘误了契约中的表达排序说明。

原生验收 API `7889` 已退出。Docker 仅停止专用容器 `repreguard-evidence-20261117-api` 和 `repreguard-evidence-20261117-pg`。保留两只容器、网络 `repreguard-evidence-20261117-net`、卷 `repreguard-evidence-20261117-pgdata`、镜像 `repreguard-evidence-local-20261117:verify`、独立 SQLite、测试账号凭据文件和全部验收产物。

复核共享 `8020`、`15432` 仍在运行，`9000` 健康接口返回 200。检测端、研究仓、旧语料与实验产物及原 `.env` 均保持原样。工作区未提交、推送或部署。
