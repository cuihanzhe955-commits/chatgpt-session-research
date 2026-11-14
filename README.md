# ChatGPT Session / Stripe 路线研究原型

本项目严格将“真实 ChatGPT Session 代付”与可验证的 Stripe 测试支付隔离。后端只接受合成凭证，不收集、传输或重放真实 Session JSON、access token，也不调用 OpenAI 未公开支付接口。

## 运行

```bash
cd backend
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
PYTHONPATH=. pytest -q
uvicorn app.main:app --reload
```

`POST /orders` 创建绑定合成凭证的订单，`POST /orders/{id}/checkout` 创建测试 Checkout，会话结果只能通过签名 Webhook 改变订单状态。生产环境应使用 PostgreSQL、Redis TTL、Celery 清理任务和 Stripe SDK 的 `Webhook.construct_event`。
