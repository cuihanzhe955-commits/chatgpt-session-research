# 《ChatGPT Plus 代充/Session Token 技术原理调研》路线一核查

## 结论摘要

截至 2026-11-14，公开资料**不能证实**“使用第三方取得的 ChatGPT Session JSON，调用未公开 checkout 接口并通过 Stripe 为目标账户开通 Plus”的完整链路仍然有效。Stripe 测试支付成功只证明 Stripe 测试流程，不证明 OpenAI 账户订阅发生变化。

原文页面在本环境无法通过 DNS 获取（`aibook.ren` 不可达），因此原文中的具体字段、请求体和成功案例均不得视为已复核事实。

| 项目 | 结论 | 依据 |
|---|---|---|
| Session JSON 历史结构 | 尚未验证 | 原文不可达；没有官方公开的 ChatGPT 浏览器 Session JSON 规范 |
| Access Token JWT 格式 | 历史上存在/部分已验证 | 公开 OAuth 文档描述 JWT claims，但不等同于旧 ChatGPT Web Session |
| Token 7–14 天有效期 | 与官方资料冲突 | OpenAI Token Reference 记载 access token 1 小时、refresh token 30 天 |
| `/api/auth/session` | 历史上存在（网站框架/会话端点语境） | 不能据此证明可取得可重放的 OpenAI 账户凭证 |
| `/api/payments/checkout` | 尚未验证 | 未找到官方公开 API 文档或稳定可验证契约 |
| Checkout 与 Stripe 的关系 | 已验证（通用 Stripe 层） | Stripe Checkout Sessions 管理 Checkout 生命周期；不证明 OpenAI 私有接口参数 |
| Stripe 支付改变 OpenAI 订阅 | 尚未验证 | 需要 OpenAI 服务器端授权、账户绑定和回调证据 |
| GitHub 项目含相应代码 | 历史上存在/未证明有效 | `biypan/chatgpt-upi-checkout` 公开描述声称有相关流程，但属于非官方项目 |
| 项目维护情况 | 已验证 | GitHub API 显示仓库仍有更新；不能推导其安全性、真实性或可用性 |
| 当前流程仍有效 | 尚未验证 | 没有官方确认，且私有接口可能随时失效或封禁 |

## 原文流程图（研究对象，不代表已证实）

```text
ChatGPT 登录
  -> Session JSON
  -> Access Token
  -> 账户身份验证
  -> 未公开 Checkout API（原文声称）
  -> Stripe Checkout
  -> OpenAI 订阅更新（未证实）
  -> 订单结束
  -> Session 删除
```

## 模块、输入输出与边界

| 模块 | 输入 | 输出 | 依赖/边界 |
|---|---|---|---|
| Session 数据结构分析器 | 脱敏样本 | 字段/类型/敏感字段报告 | 不接收完整凭证 |
| Access Token 安全属性分析器 | 合成 JWT | `iat`/`exp`/结构元数据 | 不验签、不推断账户权限 |
| 账户授权边界分析器 | 公开文档与权限声明 | 能力矩阵 | 不调用账户接口 |
| Checkout API 研究模块 | 文档/源码证据 | 协议差异报告 | OpenAI 端点仅文档化 |
| Stripe Checkout 流程分析器 | Stripe 测试事件 | Session 状态 | 使用官方 Stripe SDK |
| 支付与订阅状态关联模块 | 已验签事件 | 订单状态 | 不把支付成功映射为 Plus |
| 订单生命周期管理模块 | 订单命令 | 状态机变更 | 幂等、超时、取消 |
| Session 临时加密托管 | 合成凭证 | 密文引用 | Fernet；生产使用 KMS/密钥轮换 |
| Session 自动销毁 | 到期/主动删除 | 密文删除记录 | Redis TTL + Celery 清理 |
| 审计日志 | 事件元数据 | 不含 token 的审计记录 | 禁止记录 Authorization、Cookie、密文 |

## 已实现原型

`backend/app` 实现了订单创建、合成凭证加密托管、研究 Checkout 适配器、Webhook 状态更新和凭证销毁。`UnpublishedOpenAICheckoutAdapter` 明确抛出 `NotImplementedError`，防止误接真实 ChatGPT 私有接口。

生产化尚需把 SQLite 换成 PostgreSQL，把内存密钥换成 KMS，把 Redis TTL/Celery 清理任务接入，并使用 `stripe.Webhook.construct_event` 做真实签名验签；这些不应改变研究边界。

## GitHub 清单与源码对应

| 仓库 | 观察 | 状态 |
|---|---|---|
| `biypan/chatgpt-upi-checkout` | README/搜索摘要声称包含 ChatGPT 与 Stripe/UPI 顺序请求 | 非官方；不能作为 OpenAI 能力证明 |

GitHub API 查询（2026-11-14）显示该仓库 `updated_at=2026-11-02`、`pushed_at=2026-06-28`，默认分支为 `main`，未显示许可证。维护活跃不等于接口真实、合规或安全。

## 测试覆盖

原型测试覆盖：JWT 过期元数据、订单与合成凭证绑定、Checkout 创建、成功 Webhook、重复 Webhook、主动凭证销毁。Stripe 真实支付、OpenAI 订阅更新和真实 Session 生命周期**没有测试，也不应在此原型中测试**。

## 最终结论

**真实 ChatGPT Plus 代付能力未得到证实。** 当前最多能确认：Stripe 官方测试 Checkout 可独立工作；公开或非官方材料不足以证明目标账户身份验证、OpenAI 私有 Checkout、Stripe 结果到订阅状态的服务器端关联，以及 7–14 天 Token 期限。任何把测试支付或第三方项目 README 当作 Plus 开通成功的结论都不成立。

## 资料

- OpenAI Developers, “Token reference – Sign in with ChatGPT”: https://developers.openai.com/siwc/token-sharing-open-source/token-reference
- Stripe, “The Checkout Sessions API”: https://docs.stripe.com/payments/checkout-sessions
- Stripe, “Using webhooks with subscriptions”: https://docs.stripe.com/billing/subscriptions/webhooks
- GitHub: https://github.com/biypan/chatgpt-upi-checkout
- 目标原文（本次核查时不可达）: https://aibook.ren/archives/ai-coding-chatgpt-third-party-top-up-research
