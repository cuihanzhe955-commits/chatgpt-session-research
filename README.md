# ChatGPT Session / Stripe 路线研究平台

本项目严格将“真实 ChatGPT Session 代付”与可验证的 Stripe 测试支付隔离。后端只接受合成凭证，不收集、传输或重放真实 Session JSON、access token，也不调用 OpenAI 未公开支付接口。

## 安全边界

这是一个可运行的订单与 Stripe 测试支付研究平台，不是 ChatGPT Plus 代充工具。它拒绝真实 ChatGPT Session、Cookie、access token 和 OpenAI 未公开支付接口；所有托管凭证必须是合成测试值。Stripe 测试支付成功也不会被解释为 OpenAI 订阅开通。

## 本地运行

```bash
cd backend
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
PYTHONPATH=. pytest -q
uvicorn app.main:app --reload
```

`POST /orders` 创建绑定合成凭证的订单，`POST /orders/{id}/checkout` 创建 Stripe 测试 Checkout（配置 `STRIPE_SECRET_KEY` 时调用官方 SDK，否则使用离线适配器），会话结果只能通过签名 Webhook 改变订单状态。

## Docker 运行

```bash
cp .env.example .env
python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
# 将输出写入 .env 的 CREDENTIAL_VAULT_KEY
docker compose up --build
```

使用 Stripe CLI 转发 Webhook：

```bash
stripe listen --forward-to localhost:8000/webhooks/stripe
```

把 CLI 输出的 `whsec_...` 写入 `.env` 后重启后端。测试卡可使用 Stripe 官方测试卡 `4242 4242 4242 4242`。Celery 清理任务可通过 `POST` 之外的调度器定期调用 `app.tasks.expire_orders.delay()`。

## API 生命周期

1. `POST /orders`：创建订单并加密保存合成凭证。
2. `POST /orders/{id}/checkout`：创建 Checkout Session，订单进入 `payment_pending`。
3. `POST /webhooks/stripe`：验签并按事件幂等更新订单。
4. `GET /orders/{id}`：只返回状态和密文是否存在，不返回凭证。
5. `POST /orders/{id}/cancel` 或 `DELETE /orders/{id}/credential`：取消/销毁托管密文。
6. Celery `expire_orders`：将超时未完成订单标记为 `expired`。

生产部署仍应使用 KMS/密钥轮换、正式数据库迁移、HTTPS、限流、身份认证和独立审计存储。
