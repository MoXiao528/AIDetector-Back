# Contract Changelog

记录 OpenAPI 破坏性变更与兼容期说明。

## Unreleased
- 初始版本：建立 contract/openapi.yaml 作为单一事实来源。
- 扩展 /api/scan/detect 响应允许 polish/translation/citations 为空以支持 functions 开关。
- 更新 /api/scan/parse-files 返回结构为 results[{fileName, content, error?}] 以支持逐文件错误提示。
