# 接入网站与状态约定

## 模块边界

保留现有 SmartChatVip PHP/SQLite 商城，先用独立路径挂载测试模块。此仓库使用自己的数据库，不直接修改商城 `store.sqlite`；两个项目的订单状态及支付结果也不会自动互相同步。

建议的路径：

- `/research/`：本仓库 React 测试页面。
- `/research-api/`：本仓库 FastAPI。
- `/research-api/webhooks/stripe`：Stripe 测试 Webhook。

当前仅实现 `research-test` 示例商品，价格由后端 `PRODUCTS` 定义。以后如接入商城商品，必须在后端读取受控商品记录并保存名称、金额和币种快照，不能直接采信浏览器传来的金额。

## 请求示例

以下代码适用于自己站点的测试页面，`base` 也可用本地 Vite 代理 `/api`。

```javascript
const base = '/research-api';
const response = await fetch(base + '/orders', {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({product_id: 'research-test', simulated_credential: 'synthetic-demo'})
});
if (!response.ok) throw new Error('创建失败');
const order = await response.json();
// access_token 仅在创建响应出现一次；不要放进 URL 或日志。
const headers = {Authorization: `Bearer ${order.access_token}`};
const checkout = await fetch(`${base}/orders/${order.id}/checkout`, {method: 'POST', headers});
const state = await checkout.json();
// offline 模式 checkout_url 为 null；stripe_test 模式才有 Stripe 测试跳转地址。
```

| 方法和路径 | 作用 |
| --- | --- |
| `GET /health` | API 与数据库就绪检查 |
| `GET /products` | 服务端商品目录与支付模式 |
| `POST /orders` | 创建订单，返回一次查询密钥 |
| `GET /orders/{id}` | 按密钥查询；可能顺便清理过期订单 |
| `POST /orders/{id}/checkout` | 使用保存的价格创建/复用 Checkout，不接受金额覆盖参数 |
| `POST /orders/{id}/simulate-payment` | 仅离线模式，使用该订单查询密钥模拟成功 |
| `POST /orders/{id}/cancel` | 只取消尚未开始 Checkout 的订单 |
| `DELETE /orders/{id}/credential` | 幂等清理合成凭证，支付状态不变 |
| `POST /webhooks/stripe` | 原始请求体验签，不使用订单密钥 |

除健康、商品、创建和 Webhook 外，所有订单操作都要求 Bearer 查询密钥。订单号不是访问凭据。无权限时统一返回 404；不接受金额覆盖等不合约请求，返回 400/422；状态冲突或 Checkout 正在处理中返回 409；外部支付创建失败返回 502，原订单可重试。

## 支付与过期语义

- 订单初始有效期 30 分钟。清理任务每分钟将未支付订单标记为 `expired` 并移除合成凭证；查询时也会清理。
- `created → checkout_pending → payment_pending → paid` 是正常路径。并发 Checkout 使用数据库条件更新抢占；提供方失败可用同一个订单重试，使用相同 Stripe 幂等键。
- `checkout.session.completed` 必须同时满足 `payment_status=paid`、测试事件、测试 Session、`mode=payment`、订单 metadata、金额与币种匹配，才标记 `paid`。异步成功事件同样核验。
- 相同事件 ID 只记一次；不同 ID 的过期/失败通知不能把 `paid` 改回未支付。
- **本地过期不会使远端 Checkout 自动失效。**Stripe Checkout 链接按提供方生命周期处理。若有效测试支付通知晚到，将仍记为 `paid`，不会隐藏已经支付的事实；合成凭证不会恢复。
- 浏览器返回取消页不等于提供方取消。为避免状态不一致，已开始 Checkout 的订单不提供本地“取消成功”；可等待提供方过期。之后接入真实支付时必须另做远端关闭支付、退款及对账。
- 支付确认只清理测试凭证，不执行真实 OpenAI 订阅开通或其他自动交付。站点业务侧不得将 `paid` 当成 `delivered`。

## SmartChatVip 宝塔部署示例（尚未部署）

可先在同一台 Linux 服务器以 Docker 启动本项目，或用单独 Python 虚拟环境管理 API。不要让 API 与数据库端口直接暴露公网。

`.env` 中设置：

```dotenv
SITE_URL=https://smartchatvip.app/research
CORS_ORIGINS=https://smartchatvip.app
ROOT_PATH=/research-api
VITE_API_BASE=/research-api
PAYMENT_MODE=offline
```

保留生成的密钥。`VITE_API_BASE` 是构建时配置，修改后重新构建前端；不要把任何服务端密钥放入 `VITE_` 变量。

在前端目录执行：

```bash
npm ci
VITE_API_BASE=/research-api npm run build
```

将 `frontend/dist` 的内容复制到现有网站根目录下的 `research/` 目录。先备份现有站点，在宝塔的站点 Nginx 配置中新增以下片段，**保留原有 PHP 配置**：

```nginx
location ^~ /research-api/ {
    proxy_pass http://127.0.0.1:8000/;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    client_max_body_size 256k;
}
location /research/ {
    try_files $uri $uri/ /research/index.html;
}
```

在 Nginx `http` 层定义并在该入口使用限流规则；具体阈值按站点负载设定。启用 HTTPS，使用站点身份体系限制测试入口，再做灰度验证。确认 `/research-api/health` 正常后，才给主站加入口链接。

如果用 Compose 提供的 Nginx 容器而非复制静态文件，则需按实际挂载路径调整代理；仓库默认 Compose 是**独立本地运行**，不是已经配置好的 SmartChatVip 公网部署。

## 数据库与升级

SQLite 适合本地流程验证；多实例网站优先用 PostgreSQL。当前使用 `create_all` 初始化新库，它不负责更新已有表。旧版 `research.db` 没有金额和访问密钥摘要，启动将报 `Legacy orders schema detected`。

升级旧测试环境：停止 API/worker/beat → 备份旧库和 Fernet 密钥 → 设置新的 `DATABASE_URL` → 在新库完成验收。**不要删除旧库以消除报错。**旧订单没有可恢复的访问密钥，需要站点用户身份映射或重新签发机制后才能迁移，不能凭订单号开放查询。

正式保留历史数据的部署应在上线前引入经验证的 Alembic 迁移与回滚流程。备份必须同时覆盖数据库、`.env`/密钥和站点配置；恢复演练不能只检查文件存在。

## 验收与后续接入

1. `/health`、商品、下单、查单与错误密钥隔离通过。
2. 离线支付流程完整，测试状态与页面文案一致。
3. 配置自己的 Stripe 测试账户，在官方测试 Checkout 支付，由签名 Webhook 更新对应订单。
4. 重复、乱序、金额不匹配、未支付完成事件不导致错误状态。
5. 重启后查询数据仍存在，过期清理继续执行，备份可恢复。

目前本地回归覆盖 SQLite 与 API 状态机；CI 配有 PostgreSQL 服务测试。没有真实 Stripe 联调证据时，不把其写成“已通过”。未部署到 Azure/宝塔主站，也未将本模块订单写入主站数据库。
