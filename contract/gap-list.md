# Gap List (Frontend vs Backend)

| 模块 | 接口 | 差异 | 建议 | 优先级 | 风险 | 影响前端页面 |
| --- | --- | --- | --- | --- | --- | --- |
| Auth | POST /api/auth/login | 后端仅返回 token；前端期望 { token, user } | 扩展登录响应返回 user，或新增 /api/auth/me 并前端串联 | 高 | 高 | 登录/注册/个人中心 |
| Auth | POST /api/auth/register | 后端未明确支持；前端期望注册并返回 { token, user } | 新增注册接口并对齐 AuthResponse | 高 | 中 | 登录/注册 |
| Auth | GET /api/auth/me | 后端未明确支持；前端期望用户信息 | 新增 me 接口并返回 User | 中 | 中 | 个人中心/鉴权 |
| Scan | POST /api/scan/detect | 现状 /detect 仅接收 { text }；前端期望 { text, functions? } 且响应为富结构 AnalysisResponse | 新增 /api/scan/detect 或别名路由，补齐 functions 与 AnalysisResponse | 高 | 高 | 检测页/结果页 |
| Scan | POST /api/scan/parse-files | 后端不支持 UploadFile；前端需要 FormData files[] | 增加多文件解析接口或先提供兼容别名 | 高 | 高 | 上传解析页 |
| Scan | GET /api/scan/examples | 后端未明确支持 | 新增示例文本接口 | 中 | 低 | 检测页 |
| Scan | GET /api/scan/history | 后端未明确支持 | 新增历史列表接口 | 中 | 中 | 历史记录页 |
| Scan | GET /api/scan/history/{id} | 后端未明确支持 | 新增历史详情接口 | 中 | 中 | 历史详情页 |
| Billing | POST /api/orders | 后端未明确支持 | 新增订单创建接口，扣费后端事务化 | 高 | 高 | 订阅/购买页 |
| Billing | POST /api/orders/{id}/pay | 后端未明确支持 | 新增支付接口，扣费后端事务化 | 高 | 高 | 支付页 |
| Billing | GET /api/subscriptions/preview | 后端未明确支持 | 新增订阅预览接口 | 中 | 中 | 订阅/购买页 |
| Config | GET /api/config/faq | 后端未明确支持 | 新增 FAQ 配置接口 | 低 | 低 | FAQ/帮助页 |
| Config | GET /api/config/quotes | 后端未明确支持 | 新增 Quotes 配置接口 | 低 | 低 | 首页/营销页 |
| Support | POST /api/contact | 后端未明确支持 | 新增联系支持接口 | 中 | 中 | 联系我们页 |
