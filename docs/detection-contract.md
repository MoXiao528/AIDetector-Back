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

兼容接口：

- `POST /api/scan`

前端主路径应该继续用：

- `POST /api/v1/detect`

## 3. 当前请求语义

### `DetectRequest`

真实有效字段：

- `text`
- `editorHtml`
- `functions`
- `options`

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

## 7. `mixed` 的现状

内部结构和部分数据模型里仍然保留：

- `mixed`

但当前正式对用户展示的口径仍然是：

- `AI`
- `Human`

如果以后要重新开放 `mixed`，至少要一起检查：

1. 前端摘要显示
2. 历史记录
3. PDF 报告
4. i18n 文案
5. README
6. 测试

## 8. 文件解析接口

接口：

- `POST /api/v1/detections/parse-files`

当前状态：

- 这是隐藏的会员解析接口
- 目前只返回纯文本 `content`
- 不返回结构化 `html / blocks`

所以它当前的真实语义是“文本抽取接口”，不是“结构化文档导入接口”。

当前首页可见上传主链路已经优先在前端做本地结构化导入，所以这条隐藏接口不是排版保真主来源。

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
