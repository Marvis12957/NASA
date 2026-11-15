import { getLanguage } from "./langState";

// Gọi backend GraphRAG thật qua đường tương đối (Vite preview proxy → :8000).
async function jget(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${path} → HTTP ${r.status}`);
  return r.json();
}

// Clerk session token: AuthSync (lib/auth.jsx) đăng ký provider sau khi đăng
// nhập; các lời gọi ask/me kèm Authorization để backend nhận diện người dùng,
// lưu hồ sơ + memory theo tài khoản. Chưa đăng nhập → không có header.
let tokenProvider = null;
export function setAuthTokenProvider(fn) { tokenProvider = fn; }
async function authHeaders() {
  if (!tokenProvider) return {};
  try {
    const t = await tokenProvider();
    return t ? { Authorization: `Bearer ${t}` } : {};
  } catch { return {}; }
}

export const api = {
  health: () => jget("/health"),
  investigations: () => jget("/api/investigations").then((d) => d.investigations || []),
  investigation: (id) => jget(`/api/investigations/${encodeURIComponent(id)}`),
  scenarios: () => jget("/api/scenarios").then((d) => d.scenarios || []),
  agents: () => jget("/api/agents").then((d) => d.agents || []),
  async ask(question) {
    const r = await fetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...(await authHeaders()) },
      body: JSON.stringify({ question, language: getLanguage() }),
    });
    if (!r.ok) throw new Error(`/api/ask → HTTP ${r.status}`);
    return r.json();
  },
  // Đồng bộ hồ sơ Clerk về backend (upsert vào bảng users) sau khi đăng nhập.
  async syncMe(profile) {
    const r = await fetch("/api/me", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...(await authHeaders()) },
      body: JSON.stringify(profile || {}),
    });
    if (!r.ok) throw new Error(`/api/me → HTTP ${r.status}`);
    return r.json();
  },
  // Stream SSE từ /api/ask/stream: sự kiện trace (agent đang làm gì) → meta → delta (chữ chạy dần) → done.
  async askStream(question, sessionId, h = {}) {
    const r = await fetch("/api/ask/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...(await authHeaders()) },
      body: JSON.stringify({ question, sessionId: sessionId || undefined, language: getLanguage() }),
    });
    if (!r.ok || !r.body) throw new Error(`/api/ask/stream → HTTP ${r.status}`);
    const reader = r.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    let meta = null;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const rawEv = buf.slice(0, i);
        buf = buf.slice(i + 2);
        let ev = "message";
        let data = "";
        for (const line of rawEv.split("\n")) {
          if (line.startsWith("event:")) ev = line.slice(6).trim();
          else if (line.startsWith("data:")) data += line.slice(5).trim();
        }
        if (!data) continue;
        let payload = {};
        try { payload = JSON.parse(data); } catch { continue; }
        if (ev === "trace") h.onTrace?.(payload);
        else if (ev === "delta") h.onDelta?.(payload.text || "");
        else if (ev === "meta") { meta = payload; h.onMeta?.(payload); }
        else if (ev === "error") throw new Error(payload.message || "stream error");
      }
    }
    h.onDone?.();
    return meta;
  },
};

export const psiUrl = (id) => `https://psi.nasa.gov/physci/repo/data/investigations/${id}`;

// Tên hiển thị: tránh trùng kiểu "SAFFIRE-III · Saffire-III" khi acronym ≈ name.
export function invTitle(d) {
  const name = (d?.name || "").trim();
  const ac = (d?.acronym || "").trim();
  if (!ac) return name;
  if (!name) return ac;
  const squash = (t) => t.toLowerCase().replace(/[^a-z0-9]/g, "");
  if (squash(name) === squash(ac) || name.toLowerCase().includes(ac.toLowerCase())) return name;
  if (ac.length > 16) return name;
  return `${ac} · ${name}`;
}

// Số liệu kiểm kê trực tiếp từ Neo4j Aura ngày 15/11/2026 (20.259 node · 73.854 quan hệ).
export const DB_STATS = [
  { label: "Node tri thức", value: "20.259" },
  { label: "Quan hệ", value: "73.854" },
  { label: "Chunk văn bản", value: "6.060" },
  { label: "Tài liệu PSI", value: "450" },
];
export const CHUNK_SOURCES = {
  labels: ["ACME chung", "PSI-102 · SAME", "Web · FLEX", "PSI-117", "PSI-20", "PSI-22"],
  values: [1251, 610, 504, 368, 353, 340],
};
