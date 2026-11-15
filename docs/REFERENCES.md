# 项目引用与协议依据

## 本项目

- 源仓库：[cuihanzhe955-commits/chatgpt-session-research](https://github.com/cuihanzhe955-commits/chatgpt-session-research)。
- 页面和文档可引用上面的仓库链接。需要固定可复现版本时，请使用实际验收后的 commit 链接，不要将移动中的 `master` 当成固定版本。
- 根目录 `CITATION.cff` 提供软件引用元数据；它不代替开源许可证，也不表示 Stripe 或 OpenAI 为本项目背书。仓库尚未指定许可证，不自行推定第三方再分发许可。

## 官方依据

| 来源 | 用于本模块的规则 |
| --- | --- |
| [Stripe Webhooks](https://docs.stripe.com/webhooks) | 使用原始请求体验证签名，处理重复与重试 |
| [Stripe Checkout fulfillment](https://docs.stripe.com/checkout/fulfillment) | 从服务端确认支付状态，成功跳转不是支付证明 |
| [Stripe idempotent requests](https://docs.stripe.com/api/idempotent_requests) | 同一订单重试复用确定的幂等键 |
| [Stripe testing](https://docs.stripe.com/testing) | 只使用测试密钥与官方测试支付数据 |
| [FastAPI CORS](https://fastapi.tiangolo.com/tutorial/cors/) | 使用明确的站点来源列表 |
| [SQLAlchemy metadata](https://docs.sqlalchemy.org/en/20/core/metadata.html#creating-and-dropping-database-tables) | `create_all` 初始化表，不替代正式数据库迁移 |

本项目是独立研究和测试实现。文档中的网站接入示例是集成建议，不代表已经在该站点部署或经过真实支付验证。
