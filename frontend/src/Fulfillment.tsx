import { useEffect, useState } from "react";

type Flow = { status: string; steps_completed: string[]; credential_stored: boolean;
  next_step?: string; authorized_until?: string; order_id?: string;
  product_name?: string; amount_cents?: number; currency?: string };
type Policy = { version: string; text: string; operator_enabled: boolean };
const API = (import.meta.env.VITE_API_BASE || "/api").replace(/\/$/, "");
const steps: Record<string, string> = {
  login_confirmed: "已核实登录的是用户指定账号",
  membership_active: "已核实本次购买成功、会员到账",
  renewal_cancelled: "已确认自动续费关闭、权益到期结束",
  payment_method_removed: "已确认本次银行卡从账号移除",
  logged_out: "已退出账号，关闭登录窗口并清除本地登录资料",
  completed: "已清理临时凭据并完成本次交付",
};
const states: Record<string, string> = { awaiting_consent: "等待授权", in_progress: "待处理 / 处理中",
  completed: "单次交付已完成", revoked_cleanup_required: "授权已撤回，须处理清理与售后",
  authorization_expired_cleanup_required: "授权已过期，须处理清理与售后",
  legacy_consent_cleanup_required: "旧版授权须清理，新订单请使用 Session 授权" };
async function call<T>(path: string, token: string, method = "GET", body?: unknown): Promise<T> {
  const response = await fetch(API + path, { method, cache: "no-store", headers: {
    Authorization: `Bearer ${token}`, ...(body ? { "Content-Type": "application/json" } : {}),
  }, ...(body ? { body: JSON.stringify(body) } : {}) });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "请求失败，请核对输入和操作顺序");
  return data;
}

export function CustomerFulfillment({ id, token, paid }: { id: string; token: string; paid: boolean }) {
  const [flow, setFlow] = useState<Flow | null>(null);
  const [policy, setPolicy] = useState<Policy | null>(null);
  const [agree, setAgree] = useState(false);
  const [sessionValue, setSessionValue] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const hide = () => { if (document.hidden) setSessionValue(""); };
    document.addEventListener("visibilitychange", hide);
    return () => document.removeEventListener("visibilitychange", hide);
  }, []);
  useEffect(() => {
    let active = true;
    setFlow(null); setSessionValue(""); setAgree(false); setMessage("");
    Promise.all([call<Policy>("/fulfillment-policy", token), call<Flow>(`/orders/${id}/fulfillment`, token)])
      .then(([p, f]) => { if (active) { setPolicy(p); setFlow(f); } })
      .catch(e => { if (active) setMessage(e.message); });
    return () => { active = false; };
  }, [id, token]);
  async function act(path: string, body?: unknown, method = "POST") {
    setBusy(true); setMessage("");
    try {
      const result = await call<Flow>(`/orders/${id}/${path}`, token, method, body);
      setFlow(result); setMessage("操作已保存。平台内步骤由操作员人工执行并确认。");
    } catch (e) { setMessage((e as Error).message); }
    finally { setSessionValue(""); setBusy(false); }
  }
  return <section className="card" aria-label="单次代付授权">
    <div className="section-label">03 / SINGLE PURCHASE</div><h2>单次代付授权与交付</h2>
    <p className="hint">当前收款仍为测试模式。本页用于验证人工代付流程，不会自动登录或执行账号内支付。</p>
    <p>{states[flow?.status || ""] || "加载中…"}</p>
    {message && <p role="status">{message}</p>}
    {flow?.status === "awaiting_consent" && policy && <>
      <p>{policy.text}</p><p className="hint">本次授权有效 24 小时。授权范围仅为当前订单所列商品及金额。</p>
      <label><input type="checkbox" checked={agree} onChange={e => setAgree(e.target.checked)} /> 我已阅读并同意本次单次代付授权</label>
      <button disabled={!agree || !paid || busy || !policy.operator_enabled} onClick={() => void act("consent", { version: policy.version, accepted: true })}>确认本次授权</button>
      {!paid && <p className="hint">先完成测试付款，再授权交付。</p>}
      {!policy.operator_enabled && <p className="hint">操作后台尚未配置，暂不接收授权或登录凭据。</p>}
    </>}
    {flow && flow.status !== "awaiting_consent" && <>
      <ol>{Object.entries(steps).map(([key, text]) => <li key={key}>{flow.steps_completed.includes(key) ? "✓ " : "○ "}{text}</li>)}</ol>
      <p className="hint">以上为操作员确认记录。系统未连接第三方账号状态核验接口。</p>
      <p>系统临时凭据：{flow.credential_stored ? "加密暂存，等待领取或自动过期" : "当前未保存"}</p>
      {flow.status === "in_progress" && !flow.steps_completed.includes("membership_active") && <form onSubmit={e => {
        e.preventDefault(); void act("login-credential", { credential_type: "session", session: sessionValue });
      }}>
        <p>提交本次授权的 Session 文本（令牌或 Session JSON）。加密暂存最长 15 分钟，操作员领取后立即从在线数据库删除。提交成功不代表 Session 已验证或账号已登录。</p>
        <label>本次登录 Session<input type="password" required maxLength={32768} autoComplete="off" spellCheck={false} value={sessionValue} onChange={e => setSessionValue(e.target.value)} /></label>
        <button disabled={busy}>加密提交本次登录信息</button>
      </form>}
      <div className="actions">
        <button className="secondary" disabled={busy} onClick={() => void act("fulfillment", undefined, "GET")}>刷新交付状态</button>
        {flow.credential_stored && <button className="secondary" disabled={busy} onClick={() => void act("login-credential", undefined, "DELETE")}>立即删除临时凭据</button>}
        {flow.status === "in_progress" && <button className="text-button" disabled={busy} onClick={() => void act("revoke-consent")}>撤回尚未执行的授权并删除凭据</button>}
      </div>
    </>}
  </section>;
}

export function OperatorConsole() {
  const [open, setOpen] = useState(false);
  const [token, setToken] = useState("");
  const [orders, setOrders] = useState<Flow[]>([]);
  const [selected, setSelected] = useState("");
  const [secret, setSecret] = useState<{ credential_type: "session"; session: string } | null>(null);
  const [evidence, setEvidence] = useState("");
  const [cleanupAction, setCleanupAction] = useState("renewal_cancelled");
  const [confirmed, setConfirmed] = useState(false);
  const [message, setMessage] = useState("");
  const [history, setHistory] = useState("");
  const [busy, setBusy] = useState(false);
  const current = orders.find(o => o.order_id === selected);
  const cleanupOnly = current?.status.includes("cleanup_required");
  useEffect(() => {
    if (!secret) return;
    const timer = window.setTimeout(() => setSecret(null), 60000);
    const hide = () => { if (document.hidden) setSecret(null); };
    document.addEventListener("visibilitychange", hide);
    return () => { window.clearTimeout(timer); document.removeEventListener("visibilitychange", hide); };
  }, [secret]);
  async function load() { setOrders((await call<{ orders: Flow[] }>("/operator/orders", token)).orders); }
  async function run(fn: () => Promise<void>) {
    setBusy(true); setMessage("");
    try { await fn(); } catch (e) { setMessage((e as Error).message); }
    finally { setBusy(false); }
  }
  return <section className="card" style={{ marginTop: 24 }}>
    <button className="secondary" onClick={() => { setOpen(!open); setToken(""); setSecret(null); setOrders([]); setSelected(""); setHistory(""); }}> {open ? "退出操作后台" : "打开人工代付操作后台"}</button>
    {open && <><h2>人工代付工作台</h2><p>逐项实际执行后填写凭证编号并确认。遇到无法取消续费或移除付款方式，保留待处理状态。</p>
      <label>操作员密钥<input type="password" value={token} autoComplete="off" onChange={e => { setToken(e.target.value); setOrders([]); setSecret(null); setSelected(""); setHistory(""); }} /></label>
      <button disabled={busy || !token} onClick={() => void run(load)}>加载最近 100 笔授权订单</button>
      {message && <p role="alert">{message}</p>}
      <label>选择订单<select value={selected} onChange={e => { setSelected(e.target.value); setSecret(null); setHistory(""); setEvidence(""); setConfirmed(false); }}>
        <option value="">请选择</option>{orders.map(o => <option key={o.order_id} value={o.order_id}>{o.order_id} · {states[o.status]}</option>)}
      </select></label>
      {current && <>
        <p>本单商品：{current.product_name} · {(current.amount_cents || 0) / 100} {current.currency}（测试订单）</p>
        <ol>{Object.entries(steps).map(([key, text]) => <li key={key}>{current.steps_completed.includes(key) ? "✓ " : "○ "}{text}</li>)}</ol>
        {current.credential_stored && current.status === "in_progress" && <button disabled={busy} onClick={() => void run(async () => {
          setSecret(await call(`/operator/orders/${selected}/reveal-credential`, token, "POST")); await load();
        })}>一次性领取登录凭据并从数据库删除</button>}
        {secret && <div><p>本次领取的信息将在 60 秒后、切换标签页或确认步骤时清除显示。</p>
          <label>本次 Session<input type="password" readOnly value={secret.session} autoComplete="off" /></label>
          <button className="secondary" onClick={() => setSecret(null)}>立即清除显示</button></div>}
        {current.status !== "completed" && <form onSubmit={e => { e.preventDefault(); setSecret(null); void run(async () => {
          await call(`/operator/orders/${selected}/attest`, token, "POST", {
            action: cleanupOnly ? cleanupAction : current.next_step, confirmed: true, evidence_ref: evidence,
          }); setEvidence(""); setConfirmed(false); await load();
        }); }}>
          {cleanupOnly ? <label>授权已停止，仅允许记录清理操作<select value={cleanupAction} onChange={e => setCleanupAction(e.target.value)}>
            {["renewal_cancelled", "payment_method_removed", "logged_out"].map(key => <option key={key} value={key}>{steps[key]}</option>)}
          </select></label> : <p>下一步：{steps[current.next_step || ""]}</p>}
          <label>凭证编号（不填 Session 或卡号）<input value={evidence} required pattern="[A-Za-z0-9_-]{3,100}" maxLength={100} onChange={e => setEvidence(e.target.value)} /></label>
          <label><input type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)} /> 我已实际完成并核实这一步</label>
          <button disabled={busy || !confirmed}>记录确认</button>
        </form>}
        <button className="secondary" disabled={busy} onClick={() => void run(async () => {
          const result = await call<{ events: unknown[] }>(`/operator/orders/${selected}/audit`, token);
          setHistory(JSON.stringify(result.events, null, 2));
        })}>查看操作记录</button>
        {history && <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{history}</pre>}
      </>}
    </>}
  </section>;
}
