import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./style.css";
import { CustomerFulfillment, OperatorConsole } from "./Fulfillment";

type Product = { id: string; name: string; amount_cents: number; currency: string };
type Saved = { id: string; token: string };
type Order = { id: string; status: string; product_name: string; amount_cents: number; currency: string;
  credential_stored: boolean; payment_mode: string; checkout_url: string | null; expires_at: string; fulfillment_status: string };
const API = (import.meta.env.VITE_API_BASE || "/api").replace(/\/$/, "");
const REPO = "https://github.com/cuihanzhe955-commits/chatgpt-session-research";
const labels: Record<string, string> = { created: "待创建支付", checkout_pending: "支付创建中", payment_pending: "等待支付确认",
  paid: "测试支付已确认", cancelled: "已取消", expired: "已过期", failed: "支付失败" };
const money = (cents: number, currency: string) => new Intl.NumberFormat("zh-CN", { style: "currency", currency }).format(cents / 100);

async function request<T>(path: string, method = "GET", token?: string, body?: unknown): Promise<T> {
  const response = await fetch(API + path, { method, cache: "no-store", headers: {
    ...(body ? { "Content-Type": "application/json" } : {}), ...(token ? { Authorization: `Bearer ${token}` } : {}),
  }, ...(body ? { body: JSON.stringify(body) } : {}) });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = data.detail;
    throw new Error(typeof detail === "string" ? detail : `请求失败（${response.status}），请检查输入或服务连接。`);
  }
  return data;
}

function initialSaved(): Saved | null {
  try {
    const value = JSON.parse(sessionStorage.getItem("research-order") || "null");
    return value && typeof value.id === "string" && typeof value.token === "string" ? value : null;
  } catch { return null; }
}

function App() {
  const [products, setProducts] = useState<Product[]>([]);
  const [mode, setMode] = useState("");
  const [saved, setSaved] = useState<Saved | null>(initialSaved);
  const [order, setOrder] = useState<Order | null>(null);
  const [credential, setCredential] = useState("synthetic-demo");
  const [lookupId, setLookupId] = useState("");
  const [lookupToken, setLookupToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    request<{ products: Product[]; payment_mode: string }>("/products").then(data => {
      setProducts(data.products); setMode(data.payment_mode);
    }).catch(e => setError(e.message));
    const callback = new URLSearchParams(location.search).get("checkout");
    if (callback) setNotice("已从支付页面返回。订单状态以服务端确认结果为准，请刷新查询。");
  }, []);

  useEffect(() => {
    if (!saved) { setOrder(null); return; }
    sessionStorage.setItem("research-order", JSON.stringify(saved));
    let active = true;
    request<Order>(`/orders/${encodeURIComponent(saved.id)}`, "GET", saved.token)
      .then(data => { if (active) setOrder(data); }).catch(e => { if (active) setError(e.message); });
    return () => { active = false; };
  }, [saved]);

  useEffect(() => {
    if (!saved || order?.status !== "payment_pending") return;
    const timer = window.setInterval(() => {
      if (document.visibilityState !== "visible") return;
      request<Order>(`/orders/${encodeURIComponent(saved.id)}`, "GET", saved.token)
        .then(setOrder).catch(e => { setError(e.message); window.clearInterval(timer); });
    }, 5000);
    return () => window.clearInterval(timer);
  }, [saved, order?.status]);

  async function act(action: () => Promise<void>) {
    setBusy(true); setError(""); setNotice("");
    try { await action(); } catch (e) { setError(e instanceof Error ? e.message : "操作失败"); }
    finally { setBusy(false); }
  }

  async function refresh() {
    if (saved) setOrder(await request<Order>(`/orders/${encodeURIComponent(saved.id)}`, "GET", saved.token));
  }

  return <main>
    <header><a className="brand" href="./"><span className="brand-icon">S</span> Session Research <span className="version">/ 0.2</span></a>
      <a href={REPO} target="_blank" rel="noreferrer">源代码 ↗</a></header>
    <section className="intro"><div className="eyebrow">ORDER & CHECKOUT LAB</div><h1>把订单流程，<br />先验证清楚。</h1>
      <p>验证收款、用户授权与人工单次代付。追踪取消续费、移除付款方式、退出账号与临时凭据清理。</p>
      <div className="mode"><span className="dot" /> {mode === "offline" ? "离线模拟模式 · 不产生扣款" : mode === "stripe_test" ? "Stripe 测试模式 · 不产生真实扣款" : "正在连接服务…"}</div>
    </section>
    <aside className="boundary">订单创建框仅接受 <code>synthetic-</code> 开头的测试值；Session 在下方单独授权后临时提交，勿填入合成值框。测试收款成功不代表会员已开通。</aside>
    {error && <div className="message error" role="alert">{error}</div>}
    {notice && <div className="message" role="status">{notice}</div>}
    <div className="grid"><section className="card"><div className="section-label">01 / CREATE</div><h2>创建一笔测试订单</h2>
      {products.map(product => <div className="product" key={product.id}><div><strong>{product.name}</strong><p>一次性支付流程测试，无会员交付</p></div><b>{money(product.amount_cents, product.currency)}</b></div>)}
      <form onSubmit={e => { e.preventDefault(); void act(async () => {
        const data = await request<Order & { access_token: string }>("/orders", "POST", undefined,
          { product_id: products[0].id, simulated_credential: credential });
        setOrder(data); setSaved({ id: data.id, token: data.access_token });
        setNotice("订单已创建。查询密钥仅在创建时返回，请自行保存。");
      }); }}>
        <label htmlFor="credential">合成测试值</label><input id="credential" value={credential} onChange={e => setCredential(e.target.value)}
          pattern="synthetic-[A-Za-z0-9_-]+" maxLength={138} required autoComplete="off" spellCheck={false} />
        <p className="hint">例如 synthetic-demo。金额由服务端商品目录决定。</p>
        <button disabled={busy || !products.length}>创建测试订单 <span>→</span></button>
      </form>
      <div className="section-label lookup-label">已有订单？</div>
      <form className="lookup" onSubmit={e => { e.preventDefault(); void act(async () => {
        const data = await request<Order>(`/orders/${encodeURIComponent(lookupId.trim())}`, "GET", lookupToken.trim());
        setOrder(data); setSaved({ id: lookupId.trim(), token: lookupToken.trim() });
      }); }}>
        <label htmlFor="lookup-id">订单号</label><input id="lookup-id" value={lookupId} onChange={e => setLookupId(e.target.value)} required />
        <label htmlFor="lookup-token">查询密钥</label><input id="lookup-token" type="password" autoComplete="off" value={lookupToken} onChange={e => setLookupToken(e.target.value)} required />
        <button className="secondary" disabled={busy}>查询订单</button>
      </form>
    </section>
    <section className="card order-card"><div className="section-label">02 / TRACK</div><h2>订单进度</h2>
      {!order || !saved ? <div className="empty"><span>◎</span><h3>从一笔订单开始</h3><p>创建或查询后，在这里查看支付状态。</p></div> : <>
        <div className={`status ${order.status === "paid" ? "paid" : ""}`}>{labels[order.status] || order.status}</div>
        <dl><dt>商品</dt><dd>{order.product_name}</dd><dt>订单金额</dt><dd>{money(order.amount_cents, order.currency)}</dd>
          <dt>订单号</dt><dd className="mono">{order.id}</dd><dt>合成凭证</dt><dd>{order.credential_stored ? "已加密保存" : "已清理"}</dd>
          <dt>有效期（本地时间）</dt><dd>{new Date(order.expires_at.endsWith("Z") || /[+-]\d\d:\d\d$/.test(order.expires_at) ? order.expires_at : order.expires_at + "Z").toLocaleString()}</dd>
          <dt>交付</dt><dd>下方查看人工代付流程</dd></dl>
        <details><summary>保存订单查询密钥</summary><p className="hint">仅当前标签页暂存；关闭标签页后请用自己保存的订单号和密钥查询。</p><code className="token">{saved.token}</code>
          <button className="secondary small" onClick={() => void act(async () => { await navigator.clipboard.writeText(JSON.stringify({ order_id: saved.id, access_token: saved.token })); setNotice("订单号与查询密钥已复制。"); })}>复制查询信息</button></details>
        <div className="actions">
          {order.status === "created" && <button disabled={busy} onClick={() => void act(async () => {
            const next = await request<Order>(`/orders/${order.id}/checkout`, "POST", saved.token); setOrder(next);
            if (next.checkout_url) {
              const url = new URL(next.checkout_url);
              if (url.protocol !== "https:" || url.hostname !== "checkout.stripe.com") throw new Error("支付跳转地址不符合预期。");
              location.assign(url.href);
            } else setNotice("离线 Checkout 已创建。可继续点击模拟支付完成流程。");
          })}>创建测试支付 →</button>}
          {order.status === "payment_pending" && mode === "offline" && <button disabled={busy} onClick={() => void act(async () => {
            await request(`/orders/${order.id}/simulate-payment`, "POST", saved.token); await refresh();
          })}>模拟支付成功</button>}
          {order.status === "payment_pending" && mode === "stripe_test" && order.checkout_url && <button disabled={busy} onClick={() => {
            const url=new URL(order.checkout_url!); if(url.protocol==="https:"&&url.hostname==="checkout.stripe.com") location.assign(url.href);
          }}>继续测试支付 ↗</button>}
          <button className="secondary" disabled={busy} onClick={() => void act(refresh)}>刷新状态</button>
          {order.status === "created" && <button className="secondary" disabled={busy} onClick={() => void act(async () => { await request(`/orders/${order.id}/cancel`, "POST", saved.token); await refresh(); })}>取消订单</button>}
          {order.credential_stored && <button className="text-button" disabled={busy} onClick={() => void act(async () => { await request(`/orders/${order.id}/credential`, "DELETE", saved.token); await refresh(); })}>清理合成凭证</button>}
        </div><p className="hint">支付页面返回不会改变订单状态。进入 Checkout 后，返回或关闭页面也不代表取消支付；状态由 Webhook 或离线模拟确认。</p>
      </>}
    </section></div>
    {saved && order && <CustomerFulfillment key={saved.id} id={saved.id} token={saved.token} paid={order.status === "paid"} />}
    <OperatorConsole />
    <footer><span>测试收款 · 人工单次代付流程</span><a href={REPO} target="_blank" rel="noreferrer">引用项目：chatgpt-session-research ↗</a></footer>
  </main>;
}

createRoot(document.getElementById("root")!).render(<App />);
