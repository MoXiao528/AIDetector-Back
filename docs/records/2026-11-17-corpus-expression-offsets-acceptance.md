# 2026-11-17 表达定位与可见性后端验收

- 词库仍为 `1.0.0`，文件 SHA-256 为 `ea2793dd9b6bbc045194ccd8db8ab018006519dd961629cc879cd0a9cfe80ced`。真实最长匹配追加可选 `corpusExpressions.matches`，坐标以提交全文 Unicode 码点计数。新旧词库输出对照 148 例，覆盖 5,190 条词库项。31,608 个位置回切原文后与表达相符。
- PDF 使用保存的完整原文、摘要、段落和匹配快照。AI 显示为 0% 或 Human 为 100% 时隐藏词语区。其余情况只显示完整落在可靠对齐的 `ai` 段落中的命中。已单独复验 `raw` 正确而 `text` 损坏的快照，前端和 PDF 都要求 `text` 对应原文，非空 `raw` 也需对应。
- 复用上轮 Python 3.11 验收镜像并只读挂载本轮源码。Docker 定向回归 196 项通过，覆盖匹配器、快照、OpenAPI、HTTP 和 PDF。Ruff 与 `git diff --check` 通过。隔离 PostgreSQL 完成 Alembic `20240921_0017` 迁移。首次临时卷目录非空，改用新专用卷并按 PostgreSQL 18 镜像默认目录初始化。

## 实际 HTTP 与 PDF

隔离后端通过本机 `9000` 检测服务完成一次真实中文检测，保存历史 `5`。原文含 emoji、CRLF、标题、作者及重复正文。词表 26 项、36 处位置，其中 2 处属于 metadata。首次响应、历史详情、历史列表和幂等回放保存同一份快照。独立数据库记录 1 条已完成请求，结算 1,199 字。下游日志合计 3 个 `/detect` 请求，预检、回放、历史和 PDF 无额外下游调用。

共享验收样例中的同一词在 AI 合并段内命中两次，在同文 Human 段内也命中一次。四条合成历史通过实际 API 和 PDF 导出。重复 AI 例只显示预期三个表达，Human 100%、旧记录无坐标、仅 Human 命中三例均保留写作特点并隐藏词语区。样例从独立数据库读取。

[实际检测回执](D:/Code/AIDetector-Back/scripts/loadtest/results/corpus-offsets-20261117-f84b94a/real-acceptance.json) · [真实 PDF](D:/Code/AIDetector-Back/scripts/loadtest/results/corpus-offsets-20261117-f84b94a/actual-matched.pdf) · [四例回执](D:/Code/AIDetector-Back/scripts/loadtest/results/corpus-offsets-20261117-f84b94a/fixture-acceptance.json) · [前后端共用样例](../../contract/fixtures/corpus-expression-visibility.json)

现有 PDF 原文字体在样例中的 emoji 和兼容汉字 `神` 上显示缺字占位，表达项中的规范化字形 `神` 正常。测试使用本轮单独的 Docker API `18322` 和 PostgreSQL，现有服务、检测端、研究仓、固定词库及历史数据保持原样，未提交、推送或部署。

前端验收完成后，已停止本轮专用 API `repreguard-offsets-f84b94a-api` 和 PostgreSQL `repreguard-offsets-f84b94a-pg-v2`，确认两只容器均为 `exited`。保留容器、卷、镜像、验收文件和测试凭据。
