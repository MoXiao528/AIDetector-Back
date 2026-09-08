# Detection Contract

这份文档只描述当前真实有效的检测契约，不再把未来能力和已开放能力混写。

## 1. 当前正式开放的能力

用户主链路正式开放的只有：

- `scan`

配套能力有：

- `auth`
- `quota`
- `history`
- `reports`
- `api keys`
- `admin`
- `teams`

下面这些当前不是正式开放功能：

- `polish`
- `translate`
- `citation`

如果你在代码里看到相关字段，先把它理解成兼容保留位，不要当成线上已开放能力。

## 2. 当前检测接口

主接口：

- `POST /api/v1/detect`

兼容入口：

- `POST /api/v1/scan/detect`
- `POST /api/v1/scan`
- `POST /api/scan/detect`
- `POST /api/scan`

五个入口共享同一套推理、配额和幂等边界。主入口返回 `DetectionResponse`，四个兼容入口继续返回 `AnalysisResponse`；新集成仍应只使用 `/api/v1/detect`。

前端主路径应该继续用：

- `POST /api/v1/detect`

## 3. 当前请求语义

### `Idempotency-Key`

五个检测入口都强制要求请求头：

```http
Idempotency-Key: 7b8b9d42-fb91-4e0f-a801-8ec6172eed9a
```

规则：

- 必须是 UUID；缺失或格式错误返回 `422`
- 一次用户发起的逻辑检测生成一个新 UUID
- 同一次逻辑检测遇到超时、断线或客户端重试时，必须复用原 UUID
- 同一 actor/key 正在处理时返回 `409`，并通过 `Retry-After` 告知等待秒数
- 同一 actor/key 换了请求内容时返回 `409`，不会启动第二次推理，也不会扣额度
- 已完成记录无法再回放结果时返回 `410`；复用旧 key 不会重新推理

这是 2.0.0 的硬切协议：服务端不补 key，也不为旧客户端保留无 key 分支。调用方必须和后端一起升级。

### `DetectRequest`

真实有效字段：

- `text`
- `editorHtml`
- `functions`
- `options`

`options.repre_guard` 是服务端检测结果的保留命名空间；客户端提交该顶层键（包括大小写或首尾空格别名）会在模型调用前返回 `422`。其他非保留 `options` metadata 继续兼容。

### `functions`

当前只允许：

- `scan`

现在不要再把下面这些当成有效请求能力：

- `polish`
- `translate`
- `citation`

## 4. 当前响应语义

### `score`

归一化后的 AI 概率，范围 `0 ~ 1`。

### `rawScore`

下游检测服务原始分数。

### `threshold`

下游检测服务阈值。

### `modelName`

对用户统一展示的模型版本名，不直接暴露真实 provider 模型名。

### `result.sentences`

这个字段当前不是“自然语言句子数组”。

真实语义是：

- 检测块数组
- 通常对应 `paragraph / merged paragraph block`

所以前端虽然能做句级或块级展示，但契约里的核心单位仍然是检测块，不是稳定的 NLP sentence token。

### `result.sentences[].startParagraph / endParagraph`

表示这个检测块对应原始段落范围。

## 5. 富文本和高亮

如果请求里带了 `editorHtml`：

- 前端高亮预览优先基于原始富文本结构做局部标色
- 不再优先走纯文本重建整块 HTML

这能保住：

- 标题
- 列表
- 换行
- 强调
- 多段结构

如果没有 `editorHtml`：

- 系统会退化为基于纯文本的预览构建

## 6. 保留兼容字段

当前 `HistoryAnalysis` 和部分响应里仍然保留这些字段：

- `translation`
- `polish`
- `citations`

它们现在的真实状态是：

- `translation=""`
- `polish=""`
- `citations=[]`

这些字段存在的原因是：

- 历史记录结构稳定
- 兼容旧前端
- 给后续功能预留位

结论：

- 字段存在
- 不代表功能已上线

## 7. 二元公共分类契约

当前正式对用户展示的标签和摘要只有：

- `AI`
- `Human`

旧三分类历史仅作为内部持久化格式保留；读取时第三类标签、句段和摘要占比统一折叠进 `Human`。该投影不会迁移或改写数据库原始记录。

## 8. 文件解析边界

后端不再提供文件上传或解析接口：

- `POST /api/v1/detections/parse-files` 已硬删除，调用返回 `404`
- PDF、DOCX 和 TXT 均由浏览器在本地抽取文本
- 后端检测接口只接收抽取后的文本，不接收原始文件或 multipart 兼容请求

这不会改变当前首页和 Dashboard 的可见上传流程；它们原本就使用前端本地结构化导入逻辑。

## 9. 改动前最少要联动检查什么

### 改模型显示名

至少一起检查：

1. 后端常量
2. README
3. 本文档
4. 前端文案
5. 测试

### 改最小检测字数

至少一起检查：

1. 后端常量
2. 前端限制
3. 合并逻辑
4. README
5. 测试

### 改切段 / 合并逻辑

至少一起检查：

1. `_split_paragraphs`
2. `_merge_short_paragraphs`
3. 前端高亮映射逻辑
4. hover 联动
5. 测试

### 真正重新开放 `polish / translate / citation`

至少一起检查：

1. 前端功能入口
2. 结果 tab
3. 历史记录
4. PDF
5. OpenAPI
6. README
7. i18n
8. 测试

## 10. Evidence V1 本地计算与模式输出合同

EV4-01 已提供 `app.services.evidence_engine.EvidenceEngine` 的 Bundle 加载、响应校验、本地特征计算和 Reference 比较；
EV4-03 已接入应用生命周期和可选公共输出字段；EV4-02 已接通 Router HTTP 与本地比较，主检测分数、阈值和标签保持不变。
RepreGuard 的 D1 已本地实现内部 endpoint 和 py3langid 0.4.0 语言检查，并通过真实 Router 与主检测共存验收，整项 `DONE_LOCAL`；
EV4-04 已把 Evidence 快照接入既有结算事务。用户明确部署问题暂不处理，不阻塞后续本地开发。

### 配置与生命周期

| 配置 | 默认值 | 语义 |
|---|---|---|
| `DETECT_EVIDENCE_MODE` | `off` | `off/shadow/serve`；控制整文调用、本地计算和公开输出 |
| `DETECT_EVIDENCE_BUNDLE_PATH` | 空 | 部署提供的本地 Bundle 路径 |
| `DETECT_EVIDENCE_BUNDLE_SHA256` | 空 | 受控部署配置锁定的 64 位小写 SHA-256 |
| `DETECT_EVIDENCE_TIMEOUT_SECONDS` | `12` | HTTP 与本地比较共用的总预算；有限 `(0,60]` 秒，非法值只关闭 Evidence |

```python
from app.services.evidence_engine import EvidenceEngine

engine = EvidenceEngine(
    mode=settings.detect_evidence_mode,
    bundle_path=settings.detect_evidence_bundle_path,
    bundle_sha256=settings.detect_evidence_bundle_sha256,
    timeout_seconds=settings.detect_evidence_timeout_seconds,
)
```

主应用 lifespan 在 `app.state.evidence_engine` 持有一个实例；每次应用启动只构造一次，成功后的 `reference` 与已加载 Router 标识驻留内存，
响应校验不再读文件或重算 SHA。失败同样保留状态，重启应用才重试或应用新的配置；不实现在线配置热更新。
意外构造异常只记录固定告警并保留 `None`，不阻止主应用启动；退出时先等待已有 Evidence 任务结束，再关闭共享 HTTP 客户端。
`off` 不访问 Bundle，即使路径/SHA/超时非法也不影响基线；无效 mode/path/SHA/超时或加载失败只产生实例
`status=failed`，不会因可选配置使全局 Settings 或主服务启动失败。

加载状态为 `off/ready/failed`，失败原因只为 `invalid_evidence_config/invalid_evidence_bundle`，
不包含本地路径、JSON 内容或底层异常。这里的 `ready` 仅表示消费实例已加载，不是产品 Evidence ready 或上线批准。

Bundle 读取上限及两成员声明解压总量上限均为 32 MiB；先验证同一份读取 bytes 的外部 SHA，
再用标准库解析 ZIP，精确只接受 `manifest.json` 与 `reference.json`，不解压到磁盘。
拒绝重复成员/JSON keys、额外成员、未知 schema、非有限数、路由合同漂移及不合法 Reference cells/metrics。
只验证消费所需语义，不重做研究 source/cohort 审计、不向 Runtime 扩散其细 SHA。

成功后的 `artifact_version` 使用 Bundle 外部 SHA；`reference_schema_version=1`、`feature_schema_version=1`。
Router 身份由 Bundle 的 `router.artifact_sha256` 绑定。冻结的 `serve_eligible=false` 和两个 waiver
保持原值；这份历史事实不阻止本地加载，也不授予 serve 权限。后续 EV4/EV6 验收控制上线。
回退应恢复匹配的 Bundle/Router 组合；单独更换成不匹配的一侧只能降级 Evidence。

### EV4-03 模式与公开读取

| mode | 本地消费者 | 公开结果 |
|---|---|---|
| `off`（默认） | 不读取 Bundle，不计算 Evidence | 完全省略 `evidence` |
| `shadow` | 加载一次；分析并保存合法快照 | 完全省略 `evidence` |
| `serve` | 加载一次；分析并保存合法快照 | 有合法服务端快照才输出 `evidence` |
| 其他值 | 实例为 `failed/invalid_evidence_config` | 完全省略 `evidence`，主功能不受影响 |

可选 `evidence` 仅位于标准 `DetectionResponse` 与 `HistoryRecordResponse` 顶层，结构直接沿用下方 `analyze` 结果；
不加入 `Analysis`、检测请求或手工历史输入。公共 Evidence 只读取已校验并保存的 `meta_json.evidence`，首次响应也使用这份快照；
`options.evidence` 仍是普通用户选项，按原规则清洗，不能成为服务端 Evidence。
新检测可产生该字段；幂等回放和普通历史列表/详情只读取已有快照，不加载 artifact、不重新推理或计算、不改写存储。
缺少、畸形或未知版本的快照只被省略，不造成主结果验证失败；有效的 `failed/unsupported/insufficient/partial/ready` 结果按原状态输出。
输出类型拒绝额外嵌套字段、非有限数和未知原因，校验固定指标/维度、版本、覆盖率与状态，避免透传路径、异常和额外正文。

off/shadow 只省略 Evidence 字段，不全局排除 null，因此已有 null 字段保留。
检测列表和管理员详情共用 `project_public_meta_json`，所有模式均移除原始根级 `evidence/artifactVersion`，防止绕过专用输出字段；
其他已有元数据语义保持不变。旧 scan 兼容接口、PDF 和示例维持原白名单输出，不增加 Evidence 展示。

EV4-03 通过固定 Router 响应和预置快照验证投影；EV4-02 另用临时回环 HTTP Router、实际后端路由/客户端/特征计算验证请求链。
EV4-04 接通快照写入，首次响应、数据库、历史和幂等回放使用同一份已校验结果；公开输出仍由当前 mode 控制。

### EV4-04 快照与事务

`DetectionService.create_detection` 只接收服务端独立 `evidence` 参数，复用公开 schema 严格校验，再序列化为 JSON 基础类型。
合法结果写入既有 `Detection.meta_json.evidence`，根级 `artifactVersion` 取自同一结果；加载失败的合法快照允许该版本为 null。
客户端 options、Analysis 和手工历史输入不能创建服务端快照；不新建表或迁移。
off 不产生快照；shadow/serve 都保存合法的正常或降级结果，包括 failed/unsupported/insufficient。
校验或快照序列化失败时仅省略 Evidence 及其版本，首次响应也不输出未保存的候选结果。

快照随原检测结果、配额 ledger 和幂等完成状态一次提交；实际 flush/commit 失败沿用原事务回滚，不去掉 Evidence 重试第二次写入。
取消、过期 lease 和 owner 被接管仍按原合同处理；后台 Evidence 任务只计算，不能写快照。
提交成功后响应丢失，重放读取已提交快照，不重复模型调用、扣费或建记录。
历史读投影只校验快照 schema，不要求匹配当前 Bundle SHA、不补算或回写；切换 mode/Bundle、改名、置顶及 guest claim 都保留原快照。
因此关掉 Evidence 可隐藏旧快照，之后 serve 可再展示；旧记录没有快照时继续省略。

### EV4-02 整文调用与预算

所有五个检测入口共用 `_detect_impl`：先完成原有主检测分段和结果合并，再把原始 `payload.text` 一次性传给
`EvidenceEngine.run`；不规范化、不截断、不进入主检测重试/分段循环，不发送主标签或 score。
`run` 调用既有 `RepreGuardClient.route_evidence`，再在线程中调用已有同步 `analyze`；主标签仅供本地参考差异展示。
off、坏配置/Bundle、主检测失败或已完成幂等重放不发起 Router HTTP。

Router URL 由生效的检测 URL 派生，将末尾 `/detect` 换为 `/evidence/route`，保留 origin 和反向代理路径前缀。
不新增 URL 配置或客户端；PHP、query/fragment、userinfo、非 HTTP(S) 或不以 `/detect` 结束的地址在可选路径降级。
复用连接池、`X-RepreGuard-Token`、128 KiB 声明/流式响应上限，不重试、不跟随重定向、不解析或透传非 200 错误正文。
HTTP 200 仍须通过严格 JSON（含重复键/非有限数拒绝）、schema 与已加载 SHA 校验。
传输失败/非 200 为 `model_unavailable`，超时为 `timeout`，畸形/超大响应或 SHA 不匹配为 `invalid_router_response`；
D1 合法 `failed/unsupported` 保留原原因；这些都只影响 Evidence。

单一总预算默认 12 秒（D1 默认 10 秒）；整体截止时间也约束持续零碎返回的 HTTP 响应，不能靠每次读超时续命。
后端每进程一个活动 Evidence 任务、零等待队列，繁忙立即返回 `failed/busy`；HTTP 后的特征/比较在线程执行，避免直接占用事件循环。
调用者超时或取消后，已有任务仍计入占位，直至有界 HTTP/实际线程结束；后台任务只计算，不做数据库结算或写入。
这不是 CPU/RSS 或 native 卡死硬隔离；不启动独立 worker/进程池。shutdown 等待占位任务完成，卡死线程也会阻碍正常退出。
shadow/serve 都记录固定状态和耗时，不记录原文、token、路径或底层异常。

结算 lease 为 `max(DETECT_REQUEST_TIMEOUT, DETECT_SERVICE_TIMEOUT) + ceil(有效 Evidence 预算) + 30 秒结算缓冲`；
默认有效启用时为 162 秒，off/不可用 Bundle 保持 150 秒。Evidence 在结算事务加锁前完成或超时，不延长持锁事务。
原 owner-token 与 lease 校验不放宽：取消请求保留原未知执行状态，过期/被接管的 owner 不能写结果或扣配额。
超时正常降级后主检测可结算，已完成 key 重放不再推理、扣费或新增记录。

### D1 响应 schema v1

内部请求为 RepreGuard `POST /evidence/route`，携带既有 `X-RepreGuard-Token`，
JSON 精确为 `{"text":"用户原始整文"}`；不附主模型结果，不进入主检测分段循环。
请求体上限 128 KiB（含流式计数），text 严格要求非空白字符串、1–20,000 Unicode code points，
拒绝额外字段和类型转换。认证、体积和输入错误为非 2xx；输入校验响应不回显原文。
D1 业务结果含 `busy/timeout` 均为 HTTP 200，必须继续检查下面的 status/reason，不能仅看 HTTP 成功。

语言检查保留 py3langid 全部候选，`norm_probs=True`、不设 confidence 门。
初始化缓存公开 `rank("")`，逐请求对 NFKC/空白规范化后的整文只执行一次 `rank`：
排名与空输入完全相等、或首标签为 `und/zxx` 时返回 `failed/language_undetermined`；
首标签不在八语言时返回 `unsupported/unsupported_language`，不把 `wuu/yue/ary/arz` 映射到 `zh/ar`。
首标签受支持才执行冻结 XLM-R；不替换其最终 language/domain，也不加两模型一致性或低分拒绝门。
短文/混合文本按同一整文规则处理，不做分段投票或 Mixed；此分类器会误判，空信息检查也不覆盖所有无意义文本。

检测端默认关闭，显式启用才在启动时加载一次。Evidence 独立 1 active / 0 queued，复用既有 admission；
默认响应超时 10 秒，`REPRE_GUARD_EVIDENCE_TIMEOUT_SECONDS` 可配置为有限的 `(0,60]` 秒。
超时或请求取消后仍等工作线程实际结束才释放容量，其间返回 `failed/busy`。
关闭/加载失败返回 `failed/model_unavailable`、SHA 为 null；请求失败只影响 Evidence。
这是并发占位隔离，不是进程 CPU/RSS 或卡死线程的硬隔离；已通过本机主检测共存预算，目标部署未验收。

所有字段必须存在；无值用 `null`，所有层级拒绝额外字段。示例中的 SHA 来自当前已验收 artifact：

```json
{
  "schemaVersion": 1,
  "status": "routed",
  "routerArtifactSha256": "9a1a5be8f0d7599e682d30c7f50e3deb5f747b59ef720e288f6342e7c944f896",
  "route": {
    "language": "en",
    "domain": "academic",
    "confidence": {"language": 0.98, "domain": 0.81}
  },
  "reason": null
}
```

| status | Router SHA | route | reason |
|---|---|---|---|
| `routed` | 必须匹配已加载 Bundle | 完整对象 | `null` |
| `unsupported` | 必须匹配已加载 Bundle | `null` | `unsupported_language` |
| `failed` | 可为 `null`；有值必须匹配 | `null` | `model_unavailable/model_failure/busy/timeout/language_undetermined` 之一 |

`schemaVersion` 必须是真正的整数 `1`；SHA 必须为 64 位小写 hex。
`language` 只接受 `ar/de/en/es/fr/pt/ru/zh`；`domain` 只接受 `academic/news/novel/seo/webtext/wiki`。
两个 confidence 分别是语言边缘分数与所选语言内的条件领域分数，必须为有限的 `[0,1]` 数值，
拒绝 bool/字符串，但接受 0 和极低分数；不能用它们重新引入 abstain 或宣称路由正确概率。
不传原文、主模型 score/threshold/label、generator、本地路径、长度档或 reference fallback；
后两者属于后端特征与 Reference 消费职责。

`validate_router_response(payload)` 对合法响应返回独立副本；不合法的响应统一返回：

```json
{"schemaVersion":1,"status":"failed","routerArtifactSha256":null,"route":null,"reason":"invalid_router_response"}
```

消费者未成功加载时同形返回 `reason=bundle_unavailable`。这两个原因只由后端生成，不接受检测端提交。
此校验只确认端点的结构与身份声明，不能证明输入受支持，也不会将失败改成成功路由。

### 本地特征提取

```python
result = engine.extract_features(original_text, router_response)
```

该同步入口接收用户原始整文和 D1 合同响应，内部复用 `validate_router_response`；
仅在 Bundle 已加载、响应为 `routed` 且输入为字符串时按需导入 `evidence_features`。
不截断、预先改写或缓存用户文本，不调用模型、网络或 Reference 比较。
空字符串按冻结算法产生缺失值和资格原因；非字符串返回 `failed / invalid_text`。

内部结果固定包含：

| 字段 | 内容 |
|---|---|
| `status` | `extracted/off/unsupported/failed` |
| `artifactVersion` | 已加载的 Bundle SHA；未加载为 `null` |
| `featureSchemaVersion` | 本地消费的特征合同版本 `1` |
| `route` | 已校验的路由副本；无合法路由为 `null` |
| `features` | 成功时完整 37 个 V1 scalar 字段，否则 `null` |
| `missingReasons` | 每个缺失 scalar 的原因；未提取为 `{}` |
| `patterns` | 三类描述性计数和原文 offsets；未提取为 `null` |
| `reason` | `extracted/off` 为 `null`；其余为固定原因码 |

`extracted` 只表示特征计算完成，不是产品 Evidence ready、语言识别通过或可比较承诺。
`features` 保持研究端字段名、单位与资格规则；`length_band` 原样保留
`below_minimum/short/medium/long/above_long`，不能把越界值夹到可比较长度档。
`eligible_directional/eligibility_reason` 也原样保留，后续比较仍须同时检查长度档和 Reference。
37 个 scalar 覆盖 22 个比较指标；这个提取入口不生成 percentile、quality、relation 或 AI/Human 判断。
Reference 比较由下方 `analyze` 入口完成。

允许缺失的 NaN 转为 JSON `null`，并与 `missingReasons` 严格一一对应：
非中文的 `char_mattr_zh/repeat_char_ngram_coverage_zh` 为 `not_applicable_for_language`，
其余允许缺失项为 `insufficient_observations`。Infinity、不允许缺失的 NaN、字段或类型不匹配均失败，
不把无效数值伪装成普通缺失。未知语言或语言资源缺失不能回退到其他语言。

`patterns` 只含 `descriptive_top_tokens/repeated_phrases/sentence_start_templates`，
每项仅 `count` 和 `offsets`；不含词语、片段、原文或主模型结果。
偏移为原文的 0-based Unicode code-point 半开区间 `[start,end)`，包括原文 emoji、CRLF 和 combining marks；
前端使用 JavaScript UTF-16 索引时须转换，不能直接套用。各组最多 20 个偏移；完整计数不截断，
高频词按语言最多 10 项（中文 20 项），重复短语和句首模板各最多 10 项。

`off`、加载失败、无效路由及合法 `unsupported/failed` 响应均不导入特征依赖或执行提取。
加载失败保留实例原因；Router 失败保留校验后的固定原因；特征依赖缺失、schema 不兼容、
计算或资源错误统一为 `feature_extraction_failed`。失败不返回半成品，不泄漏底层异常或用户文本，
也不改变 Bundle 的加载状态；后续请求可以独立计算。

提取器迁自研究仓库 `074fe307108d3ba3e3826062be836800cbd61ec3` 的 `src/features.py`，
既有算法和内嵌资源不变，只增加无 Arrow 依赖的 JSON 规范化；八语言 synthetic golden 原样迁入测试。
依赖声明为 `numpy>=2.2,<2.3`、`jieba==0.42.1`；本地验证环境为 NumPy `2.2.5` / Jieba `0.42.1`，
本批未安装依赖。Jieba 字典初始化与计算成本、真实部署兼容性和资源预算仍需后续验收。

### 本地 Reference 比较

```python
result = engine.analyze(original_text, router_response, main_label="AI")
```

入口复用一次 `extract_features`，接收原始整文、D1 合同响应和主检测已有标签。
`main_label` 只接受大小写不敏感的 `AI/Human`，非法值返回 `failed / invalid_main_label` 且不提取。
它只用于逐项差异提示；入口不接收 score/threshold，不计算新标签，也不改变主检测结果。

先同时要求 `eligible_directional=true` 和 `length_band=short/medium/long`；
即使超长文本的原资格字段为 true，也不能比较。资格不足时保留观察值、描述性 patterns 和原因。
Reference 必须存在合法 exact key，再按 `exact → language_length → language` 选择第一个
`source_group_count>=10` 的完整 cell；不跨语言，选定后不为缺失指标另找参考组。

顶层结果固定包含：

| 字段 | 内容 |
|---|---|
| `status` | `ready/partial/insufficient/off/unsupported/failed` |
| `artifactVersion/featureSchemaVersion` | 已加载 Bundle SHA 或 `null` / 整数 `1` |
| `route` | 校验后的路由加原始 `lengthBucket` 与 `fallbackLevel`；未提取为 `null` |
| `quality` | `level/coverage/reasons`；可比较项数固定除以 **22** |
| `signals` | 特征提取成功时固定 22 项，缺项不删除；否则 `[]` |
| `patterns` | 提取入口的三类描述性计数和原文 offsets；未提取为 `null` |

22/22 为 `ready`，1–21/22 为 `partial`，0/22 或资格/参考组不可用为 `insufficient`；
这三个状态下 `quality.level=status`，其余状态的 level 为 `unavailable`、coverage 为 0。
备用组在 `fallbackLevel` 和 `reference_fallback_language_length/reference_fallback_language` 原因中披露，
不额外扣覆盖率或质量等级。当前冻结 Bundle 在全部 144 条支持路由上最多可比 19 项，
三个段落衔接指标没有参考数据；完整观察值也只能得到 `19/22`、`partial`，不能改写为 `19/19`。
覆盖率只表示数据完整程度，不表示判断正确率或上线资格。

每个 signal 固定包含：

| 字段 | 内容 |
|---|---|
| `dimension/metric` | 四维分类与冻结指标名 |
| `observed` | 原单位观察值；允许缺失为 `null` |
| `humanPercentile/aiPercentile` | 两侧 0–100 近似百分位；不可比为 `null` |
| `referenceRanges` | `{human:[Q05,Q95],ai:[Q05,Q95]}`；不可比为 `null` |
| `relation` | `{human:below/within/above,ai:below/within/above}`；不可比为 `null` |
| `notice` | `reference_mismatch/outside_both/null`，规则见下 |
| `sampleCount` | 选定 cell 中该指标的配对共同有效 source-group 数；保留实际 `0` 或 `1–9`，无 cell 为 `null` |
| `offsets` | 只对重复短语和句首重复指标映射对应 patterns 的原文区间；其余为 `[]` |
| `reasons` | 无法比较的具体原因，可同时包含观察值缺失与参考数据缺失；可比为 `[]` |

近似百分位只使用已验证的 101 个 `inverted_cdf` 分位点 q：
`clamp((bisect_left(q,x)+bisect_right(q,x)-1)/2,0,100)`。
例如常量分布中等于常量为 50、低于为 0、高于为 100；重复值取分位点秩中点。
这不是精确 ECDF、插值、AI 来源概率或 Router confidence。

逐项展示规则使用两侧 **Q05–Q95 闭区间的值边界**：低于为 `below`、高于为 `above`，含端点为 `within`。
不能用近似百分位是否处于 5–95 代替值比较；并列值可能跨越分位边界，常量区间也有效。
仅当主标签对应的一侧在区间外、另一侧在区间内，才提示 `reference_mismatch`；
两侧都在区间外为 `outside_both`，其余为 `null`。这些是用户批准的 Runtime 描述性展示规则，
不是已验证的来源方向、置信区间或分类器；不得汇总投票、生成方向分数或覆盖主标签。
Bundle 冻结的 `descriptive_percentiles_only_no_direction_or_product_relation` 保持原值，
不能把本次展示规则倒写为研究验证结论。内容词 offsets 仍只作描述，不归因于 AI/Human。

提取前失败沿用既有固定原因；比较期间任何异常只返回 `failed / comparison_failed`，
清除当次 route/signals/patterns，不发布部分比较或异常内容，且不污染已加载实例及后续请求。
单项数据缺失会保留该项及原因；整体 quality 另以 `missing_observations/reference_metrics_unavailable` 披露，
资格通过并选定参考组后仍没有可比项时增加 `no_comparable_metrics`，无参考组为 `reference_cell_unavailable`。
资格失败沿用提取器原因，长度越界另有 `length_out_of_range`。以上都只影响 Evidence。

### 验证

在仓库根目录、使用现有开发环境执行；普通测试使用合成 Bundle，不依赖研究仓库：

```powershell
python -B -m pytest -q -p no:cacheprovider backend/tests/test_evidence_http.py backend/tests/test_evidence_modes.py backend/tests/test_detection_idempotency.py backend/tests/test_evidence_engine.py backend/tests/test_evidence_features.py backend/tests/test_config.py
```

真实产物 smoke 显式设置 `EVIDENCE_TEST_BUNDLE_PATH` 为已验收 Bundle 的绝对路径再运行同一命令；
该测试只读加载，并锁定当前 Bundle SHA。未配置时只跳过此 smoke，不下载、复制或重建任何研究产物。
测试覆盖单项缺失、N=9/10 与 cell-atomic fallback、并列/常量/端点、固定分母、逐项差异及失败隔离；
真实 Bundle smoke 还执行原文特征提取与 19/22 本地比较，不加载 Router 模型。
`language_undetermined` 已覆盖合法/非法状态组合及无特征依赖时的失败透传，schema 仍为 v1。
现有模式/合同测试同时读取静态 OpenAPI，确认 `EvidenceSignal.notice` 的 nullable 枚举包含 `null` 和两个合法提示值，防止合同拒绝正常响应。
D1 真实模型与本机共存、模式公共投影、EV4-02 受控 HTTP 整文单次调用/超时/并发/lease，以及 EV4-04 快照事务、回放和本机真实模型业务链已验收。
数据库回滚/提交后断线恢复使用独立文件 SQLite；既有 PostgreSQL opt-in 测试仍需其专用环境，不把 SQLite 结果表述为 PostgreSQL 或部署验收。

完整真实模型验收单独运行 `backend/tests/test_evidence_runtime_real.py`，普通全量测试默认跳过；复用 RepreGuard 的
`smoke_evidence_runtime.py --worker`，加载现有真实模型、关闭单元测试 tokenizer 替身，经实际注册/登录/检测 API 和独立 SQLite 完成全链。
临时回环服务只在本次测试存活，不访问已有业务数据库；不会下载、安装或修改持久配置。使用已有本地 NVIDIA 模型环境，显式提供以下进程环境变量：

| 环境变量 | 输入 |
|---|---|
| `RUN_EVIDENCE_REAL_RUNTIME` | `1` |
| `EVIDENCE_REAL_REPREGUARD_ROOT` | 现有 RepreGuard 仓库绝对路径 |
| `EVIDENCE_REAL_ROUTER_MODEL_PATH` | 已验收 production-final/model 目录绝对路径 |
| `EVIDENCE_REAL_MAIN_MODEL_PATH` | 现有 DetectRL-X 主模型本地目录绝对路径 |
| `EVIDENCE_REAL_LID_SITE` | 现有 py3langid 0.4.0 隔离 site 目录绝对路径 |
| `EVIDENCE_TEST_BUNDLE_PATH` | 已验收 Bundle 绝对路径 |
| `EVIDENCE_REAL_REPORT_PATH` | 现有输出目录下尚不存在的 `.json` 报告绝对路径；保存结果、版本和摘要，不存原文或密钥 |

```powershell
python -B -m pytest -q --tb=short -p no:cacheprovider backend/tests/test_evidence_runtime_real.py
```

执行后恢复这些进程环境变量；若执行账户无权访问默认 pytest 临时目录，使用工作区内确认不存在的新目录作为 `--basetemp`，不要复用已有目录。
2026-09-07 本机通过报告为 `scripts/loadtest/results/evidence-runtime-20260907-ev404-02.json`（本地忽略产物）：
三种 mode 共 7 次检测；EN/ZH 为 partial、意大利语为 unsupported；四次整文 Router 调用、三次 XLM-R forward，
首次响应/已存快照/历史/回放一致，读取不再调用模型或扣费，主 score/rawScore 差为 0、阈值/标签不变。
首轮中文样本低于冻结的 600 汉字门，正确返回 insufficient，与脚本 partial 预期不符；仅扩充固定样本后重跑，原失败报告保留，不修改资格规则。
验收是小样本业务正确性检查，不作 P95 性能结论；启动约 19.31 秒，首个主请求有约 11.48 秒冷启动，启用模式四次总请求约 0.078–0.657 秒。
资源保留门未触发，服务正常退出、活动计数归零。部署问题按用户决定暂不处理。
