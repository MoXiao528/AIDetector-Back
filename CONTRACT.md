# Contract Governance

本项目采用 **Contract-first** 流程：任何字段、路径或响应结构的变更，必须先修改 `contract/openapi.yaml`，再改实现与前端。OpenAPI 是唯一真实标准，前端类型必须由契约生成或同步。

## 命名规则
- 所有对外字段使用 **camelCase**。
- 后端 Pydantic Response 必须输出 camelCase（建议统一使用 `alias_generator`）。

## 错误结构
所有错误响应统一结构（示例）：

```json
{
  "code": 404,
  "message": "Not Found",
  "detail": {}
}
```

字段说明：
- `code`: 业务错误码（数字或字符串）。
- `message`: 面向用户的错误描述。
- `detail`: 可选对象或数组，包含字段级错误或调试信息。

## 分页字段
若接口返回列表并支持分页，统一响应字段：
- `items`: 数据数组
- `page`: 当前页（从 1 开始）
- `pageSize`: 每页大小
- `total`: 总条数

## 鉴权 Header
所有需要鉴权的接口统一使用：

```
Authorization: Bearer <token>
```

## 时间格式
时间字段使用 ISO 8601 标准，UTC 时区：

```
2024-01-01T12:00:00Z
```

## 变更流程
1. 先改 `contract/openapi.yaml` 并更新 `contract/changelog.md`。
2. 再实现后端/前端变更。
3. PR 必须勾选契约与测试清单（见 PR 模板）。
4. CI 将校验 OpenAPI 格式，格式错误必须失败。
