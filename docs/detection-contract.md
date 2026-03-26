# 检测接口约定

这页只记录当前检测链路的真实约定，以及以后最可能改动的地方。

不要把这页当成产品宣传文案，它是给后端和前端开发一起看的。

## 1. 下游算法返回

UM 算法服务当前只返回这 4 个核心字段：

```json
{
  "score": 2.78,
  "threshold": 2.49,
  "label": "AI",
  "model_name": "Qwen/Qwen2.5-0.5B"
}
```

其中：

- `score`：原始模型分
- `threshold`：判定阈值
- `label`：下游自己的判定结果
- `model_name`：下游真实模型名

## 2. 后端如何转换

后端不会把下游原始分直接给前端展示，而是先做归一化：

```text
prob_ai = 1 / (1 + exp(-(raw_score - threshold)))
```

说明：

- `raw_score == threshold` 时，展示值约为 `50%`
- `raw_score > threshold` 时，更偏向 AI
- `raw_score < threshold` 时，更偏向 Human

当前代码位置：

- `backend/app/api/v1/detections.py::_normalize_score`

## 3. 输入与切段规则

### 3.1 最低输入要求

- 整体输入必须至少 `200` 个非空白字符
- 这个限制是产品规则，不是协议真理

当前代码位置：

- 后端：`backend/app/api/v1/detections.py::MIN_DETECT_VISIBLE_CHARS`
- 前端：`AIDetector-Web/src/pages/ScanPage.vue::minDetectChars`

如果以后要改这个值，必须一起改：

1. 后端校验
2. 前端按钮禁用和提示
3. 段落合并逻辑
4. README
5. 测试

### 3.2 段落定义

- 按换行切段
- 空段不参与检测

当前代码位置：

- `backend/app/api/v1/detections.py::_split_paragraphs`

### 3.3 短段合并

- 少于 `200` 个非空白字符的相邻段落会自动合并检测
- 如果最后剩余一小段不足 `200`，会并回上一个检测块

当前代码位置：

- `backend/app/api/v1/detections.py::_merge_short_paragraphs`

## 4. 聚合规则

整篇文本的总 AI 概率不是简单平均，而是按检测块的非空白字符数加权平均。

当前代码位置：

- `backend/app/api/v1/detections.py::_detect_impl`

## 5. 对外展示模型名

当前对用户展示的模型版本固定为：

```text
v1.0
```

真实上游模型名不会直接暴露给用户，而是内部保留在：

- `provider_model_name`

当前代码位置：

- `backend/app/api/v1/detections.py::DISPLAY_MODEL_NAME`

以后如果要切模型，至少一起检查：

1. `DISPLAY_MODEL_NAME`
2. README
3. 前端结果卡片文案
4. 测试样例

## 6. 前端展示约定

### 6.1 结果卡片

右侧结果卡片展示的是“检测块”，不是“原始段落”。

所以：

- 单段：`段落 3`
- 合并块：`段落 2-4 · 合并检测`

### 6.2 预览区

预览区始终保持原始输入段落结构，不会因为合并检测而把多段压成一段。

如果多个原始段落属于同一个检测块：

- 它们会保持分段显示
- 颜色相同
- hover 右侧卡片时，左侧对应段落会联动高亮

当前代码位置：

- `AIDetector-Web/src/store/scan.js::buildHighlightedHtml`
- `AIDetector-Web/src/pages/ScanPage.vue::setActiveSentence`

## 7. 当前主路径只展示什么

当前用户主路径只正式展示：

- `AI`
- `Human`

虽然内部结构里还保留了 `mixed` 字段，但当前不作为正式对外分类展示。

原因：

- 现阶段模型能力和产品话术都只以 AI / Human 为主
- 保留 `mixed` 字段是为了避免一次性改动过大

如果以后要重新放开 `mixed`，至少一起检查：

1. 前端摘要卡
2. 历史摘要
3. 历史列表摘要行
4. PDF 报告
5. i18n 文案

## 8. 当前功能开关

用户主路径当前只开放：

- `scan`

下面这些入口暂不作为正式功能开放：

- `polish`
- `translate`
- `citation`

当前代码位置：

- `AIDetector-Web/src/pages/ScanPage.vue::allowedFunctionKeys`

如果以后要重新开放，必须一起检查：

1. 右侧 Scan Menu
2. 结果 tab
3. 后端 contract
4. README
5. 测试

## 9. 返回字段语义

核心接口：`POST /api/v1/detect`

当前响应中几个重要字段的语义是：

- `score`：后端换算后的 AI 概率，范围 `0~1`
- `rawScore`：下游原始 `score`
- `threshold`：下游阈值
- `modelName`：当前对用户展示的模型版本，固定为 `v1.0`
- `result.sentences`：检测块数组
- `result.sentences[].startParagraph / endParagraph`：检测块对应的原始段落范围

注意：

- `result.sentences` 现在不是“句子数组”
- 它的真实语义是“检测块数组”

## 10. 改动前的最小检查清单

如果你要改下面这些点，不要只改一处：

### 改模型

检查：

1. `DISPLAY_MODEL_NAME`
2. README
3. `docs/detection-contract.md`
4. 前端文案
5. 测试

### 改最低字数

检查：

1. 后端常量
2. 前端限制
3. 合并逻辑
4. README
5. 测试

### 改切段方式

检查：

1. 后端 `_split_paragraphs`
2. 合并逻辑
3. 前端预览重建逻辑
4. hover 联动逻辑
5. 测试

### 重新开放 mixed / polish / translate / citation

检查：

1. 前端功能入口
2. 结果 tab
3. 历史页
4. PDF
5. README
6. i18n
