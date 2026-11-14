import React from "react";
import { createRoot } from "react-dom/client";

function App() {
  return <main><h1>ChatGPT Session Research</h1><p>隔离原型：仅使用模拟凭证与 Stripe 测试协议，不处理真实 ChatGPT Session。</p></main>;
}

createRoot(document.getElementById("root")!).render(<App />);
