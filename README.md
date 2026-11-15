# ChatGPT Session / Stripe 路线研究

一个可独立运行、可通过反向代理接入网站的**订单、测试收款与人工单次代付流程模块**。FastAPI + React + SQLite/PostgreSQL + Celery，包含商品目录、订单查询密钥、Checkout、验签 Webhook、用户授权、临时凭据和操作后台。

项目仓库：[cuihanzhe955-commits/chatgpt-session-research](https://github.com/cuihanzhe955-commits/chatgpt-session-research)。引用本项目可使用仓库链接或根目录 `CITATION.cff`；协议细节引用见 [官方参考资料](docs/REFERENCES.md)。

## 当前能做什么

| 功能 | 状态 |
| --- | --- |
| 创建订单、固定商品价格快照、查询状态 | 已实现 |
| 每笔订单独立 Bearer 查询密钥，数据库仅保存密钥摘要 | 已实现 |
| 同一订单重复 Checkout、Stripe 幂等键、并发创建保护 | 已实现 |
| Stripe 原始请求体验签，金额/币种/订单归属核对，事件去重 | 已实现 |
| 未支付完成事件等待确认，乱序事件不覆盖已支付状态 | 已实现 |
| 过期、取消、支付确认时清理合成凭证；每分钟清理调度 | 已实现 |
| React 下单、查单、测试支付、刷新、取消及清理 | 已实现 |
| 离线完整流程 | 已验证，零外部支付请求 |
| Stripe 测试 SDK 调用 | 已实现；单元测试替身验证，需自己的测试账户联调 |
| 单次代付授权、人工操作台、逐项凭证编号、撤回授权 | 已实现，见 [单次代付流程](docs/SINGLE_PURCHASE.md) |
| 临时 Session 加密暂存、一次性领取、到期清理 | 已实现；须启用操作员密钥与清理调度，当前收款仍是测试模式 |
| 真实扣款、真实会员开通、自动交付、退款、站点会员系统 | 未实现 |

订单创建接口只接收合成测试值；独立的单次授权流程使用 Session 临时凭据，不调用 OpenAI 未公开支付接口。合成值格式仅为 `synthetic-` 加字母、数字、下划线或连字符。`paid` 仅表示测试支付已确认，不表示 OpenAI 订阅已开通。

新人工流程在独立授权后可暂存 Session 文本（令牌或 Session JSON）；不再收集账号密码，不复用旧的合成凭证字段。账号内的登录、付款、取消自动续费、移除付款方式和退出登录由操作员执行，本系统记录其确认与凭证编号，不会冒充平台端验证。用户不授权额外消费或后续续费。

## 快速启动：离线模式

需要 Python 3.12+、Node.js 22.12+/24，终端位于仓库根目录。

```bash
python scripts/init_env.py
python -m venv .venv
# Windows PowerShell 使用 .venv\Scripts\Activate.ps1
source .venv/bin/activate
pip install -r backend/requirements.txt
cd backend
python -m pytest -q
python -m uvicorn app.main:app --env-file ../.env --host 127.0.0.1 --port 8000
```

另开一个终端：

```bash
cd frontend
npm ci
npm run dev
```

打开 <http://localhost:5173>。默认离线模式不需要 Stripe 账户；创建订单 → 创建测试支付 → 模拟支付成功 → 查询 `paid`。离线 Checkout 不生成假的可访问 Stripe 域名。

`init_env.py` 只在 `.env` 不存在时生成持久 Fernet 密钥及本地数据库密码，不覆盖已有配置。保留密钥以便重启后仍可解密；不要提交 `.env`。

## Docker Compose

```bash
python scripts/init_env.py
docker compose up --build -d
docker compose ps
```

打开同样的 <http://localhost:5173>。Compose 包含前端、API、PostgreSQL、Redis、worker 和独立 beat 调度器；数据库与 Redis 不映射公网端口，API/前端仅绑定宿主机回环地址。PostgreSQL 数据保存在 `postgres_data` 卷。`docker compose down` 保留数据；不要随意使用 `down -v`。

## Stripe 测试模式

1. 编辑 `.env`：`PAYMENT_MODE=stripe_test`，填写 `sk_test_...` 与 `STRIPE_WEBHOOK_SECRET=whsec_...`。不接受 `sk_live_`。
2. 使用官方 Stripe CLI：`stripe listen --forward-to localhost:8000/webhooks/stripe`，将该次监听输出的签名密钥写入配置，重启 API。
3. 在页面创建订单并前往 Stripe 测试 Checkout。使用官方测试卡数据，不输入真实银行卡。
4. 支付状态只由验签事件改变。跳转成功页、浏览器刷新及 `invoice.paid` 都不会直接标记订单已支付。

测试模式关闭离线模拟接口。Webhook 未配置签名密钥时返回 503，不会降级接受未验签 JSON。真实 Stripe 账户联调仍需由部署者完成；不要用独立 `stripe trigger` 创建的无关 Session 判断本模块订单是否成功。

## API 与接入

交互文档：<http://localhost:8000/docs>。完整请求示例、状态语义、宝塔/Nginx 配置和旧版迁移限制见 [网站接入指南](docs/INTEGRATION.md)。

创建订单后，`access_token` 仅在该响应出现一次。后续查询、Checkout、取消和清理都必须带 `Authorization: Bearer <access_token>`。前端只在当前标签页的 `sessionStorage` 暂存查询信息，请自行保存。删除合成凭证不会取消一个已创建的支付 Session。

**旧版数据库不兼容本次订单字段更新。**程序检测到旧表时会明确停止启动，不会自动删除或改写旧数据。请先备份、使用新的测试数据库，再按接入指南规划迁移。

## 验证与范围

```bash
cd backend && python -m pytest -q
# 另一个终端
cd frontend && npm ci && npm run build
```

CI 会运行 SQLite 回归测试、PostgreSQL 回归测试与 TypeScript/前端构建。Docker 和真实 Stripe 联调是否实际通过，应以对应环境中的执行结果为准。

这是网站集成前的测试模块，尚不构成真实支付商城。正式公网接入还需站点登录身份绑定、入口限流、备份恢复演练、数据库迁移方案与真实业务验收。当前没有改动 SmartChatVip 已部署的网站。
