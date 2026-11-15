import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Search, MessageCircle, LineChart, Columns2, FileText, Settings, MessagesSquare,
  Plus, Moon, Sun, ChevronDown, X, Paperclip, Mic, ArrowUp, Trash2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@/components/ui/dialog";
import ChatMessage from "@/components/ChatMessage";
import ProfilePane from "@/components/ProfilePane";
import DataPane from "@/components/DataPane";
import ComparePane from "@/components/ComparePane";
import ReportPane from "@/components/ReportPane";
import { api, DB_STATS, CHUNK_SOURCES } from "@/lib/api";
import { AuthArea, AuthGate, AuthSync, clerkEnabled } from "@/lib/auth";
import { LanguagePicker } from "@/lib/language";

const PROMPTS = [
  { label: "Thí nghiệm quan trọng nhất", text: "Thí nghiệm nào quan trọng nhất cho an toàn cháy trên ISS?" },
  { label: "Xem hình ảnh so sánh", text: "So sánh ngọn lửa ở Trái Đất và trong vi trọng lực" },
  { label: "Xem biểu đồ tương tác", text: "Cho tôi xem biểu đồ dữ liệu nghiên cứu cháy vi trọng lực trong database" },
];

const PANE_META = {
  profile: { glyph: "✳", title: "Flame · Trợ lý nghiên cứu", sub: "Hồ sơ & công cụ của agent", h1: null, tab: null },
  data: { icon: LineChart, title: "Thư viện NASA PSI", sub: "Nghiên cứu · nguồn · CSV", h1: "Thư viện nghiên cứu", tab: "Dữ liệu" },
  compare: { icon: Columns2, title: "So sánh", sub: "Nghiên cứu và kịch bản đã lưu", h1: "So sánh", tab: "So sánh" },
  report: { icon: FileText, title: "Báo cáo nghiên cứu", sub: "Xuất bản báo cáo có nguồn", h1: "Báo cáo", tab: "Báo cáo" },
};

const NAME_TO_ID = [
  [/saffire[-\s]?iii/i, "PSI-100"], [/saffire[-\s]?ii/i, "PSI-99"], [/saffire/i, "PSI-98"],
  [/same[-\s]?r/i, "PSI-101"], [/\bsame\b/i, "PSI-102"],
  [/bass/i, "PSI-25"], [/flex/i, "PSI-69"], [/acme/i, "PSI-10"],
];

function detectInvestigation(q) {
  const m = q.match(/PSI[-\s]?(\d+)/i);
  if (m) return `PSI-${m[1]}`;
  for (const [re, id] of NAME_TO_ID) if (re.test(q)) return id;
  return null;
}

async function buildEnrichment(q) {
  const lower = q.toLowerCase();
  const out = { blocks: [], artifact: null };
  try {
    if (/(biểu đồ|dữ liệu|database|bao phủ|thống kê|chunk)/i.test(lower)) {
      out.blocks.push(
        { type: "stats", items: DB_STATS.map((d) => ({ label: d.label, value: d.value })) },
        { type: "barchart", title: "Chunk văn bản theo nguồn nghiên cứu", labels: CHUNK_SOURCES.labels, values: CHUNK_SOURCES.values, caption: "Số liệu kiểm kê trực tiếp từ Neo4j Aura, 15/11/2026. Phân bố lệch về ACME/SAME — cần chuẩn hoá theo nguồn khi dùng cho mô hình." },
      );
      if (!out.artifact) out.artifact = { tab: "data", title: "Thư viện NASA PSI", sub: "Mở artifact bên phải mà không rời chat" };
    }
    const ids = [...q.matchAll(/PSI[-\s]?(\d+)/gi)].map((m) => `PSI-${m[1]}`);
    if (ids.length >= 2 && /(so sánh|khác|compare)/i.test(lower)) {
      const [a, b] = await Promise.all([api.investigation(ids[0]), api.investigation(ids[1])]);
      const o2 = (d) => d.oxygen || (d.oxygenMin != null ? `${d.oxygenMin}–${d.oxygenMax}%` : "—");
      out.blocks.push({
        type: "facts", title: `So sánh nhanh ${a.id} ↔ ${b.id}`,
        rows: [
          { k: "Tên", v: `${a.name} ↔ ${b.name}` },
          { k: "Họ", v: `${a.family} ↔ ${b.family}` },
          { k: "Oxy", v: `${o2(a)} ↔ ${o2(b)}` },
          { k: "Nhiên liệu", v: `${(a.fuels || []).join(", ")} ↔ ${(b.fuels || []).join(", ")}` },
          { k: "Lần chạy", v: `${a.runCount ?? "—"} ↔ ${b.runCount ?? "—"}` },
        ],
      });
      out.artifact = { tab: "compare", title: "So sánh nghiên cứu", sub: "Mở bảng so sánh đầy đủ bên phải" };
    } else {
      const invId = detectInvestigation(q);
      if (invId) {
        const d = await api.investigation(invId);
        const o2 = d.oxygen || (d.oxygenMin != null ? `${d.oxygenMin}–${d.oxygenMax}%` : "Chưa trích xuất");
        out.blocks.push(
          { type: "stats", items: [
            { label: "Mã hồ sơ", value: d.id },
            { label: "Oxy", value: o2.replace(" oxygen", ""), sub: "khoảng đo" },
            { label: "Lần chạy", value: d.runCount != null ? String(d.runCount) : "—" },
            { label: "Luồng khí", value: d.flow || "—" },
          ]},
          { type: "invcard", psiId: d.id, name: d.name, family: d.family, blurb: d.blurb || d.reason || "", fuels: d.fuels || [] },
          { type: "facts", title: "Chi tiết hồ sơ", rows: [
            { k: "Họ thí nghiệm", v: d.family },
            { k: "DOI", v: d.doi || "—" },
            { k: "Cùng dòng", v: (d.peers || []).map((p) => `${p.name} (${p.id})`).join(" · ") || "—" },
          ]},
        );
        if (!out.artifact) out.artifact = { tab: "data", title: "Thư viện NASA PSI", sub: "Mở artifact bên phải mà không rời chat" };
      }
    }
    if (/(báo cáo|kịch bản|brief)/i.test(lower) && !out.artifact) {
      out.artifact = { tab: "report", title: "Báo cáo nghiên cứu", sub: "Mở artifact bên phải mà không rời chat" };
    }
  } catch { /* làm giàu thất bại không được làm hỏng câu trả lời chính */ }
  return out;
}

export default function App() {
  const [dark, setDark] = useState(() => localStorage.getItem("flame-dark") === "1");
  const [pane, setPane] = useState(null); // 'profile' | 'data' | 'compare' | 'report' | null
  const [drawer, setDrawer] = useState(false);
  const [settings, setSettings] = useState(false);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [health, setHealth] = useState(null);
  const [investigations, setInvestigations] = useState([]);
  const [invLoading, setInvLoading] = useState(true);
  const [scenarios, setScenarios] = useState([]);
  const [agents, setAgents] = useState([]);

  const [threads, setThreads] = useState(() => {
    try { return JSON.parse(localStorage.getItem("flame-threads") || "[]"); } catch { return []; }
  });
  const [threadId, setThreadId] = useState(null);
  const [messages, setMessages] = useState([]);
  const scrollRef = useRef(null);
  const taRef = useRef(null);
  const sessionRef = useRef(null);
  // ——— Animation avatar bay (đúng source gốc: fly 520ms cubic-bezier(.22,1,.36,1)) ———
  const sourceAvatarRef = useRef(null);
  const targetAvatarRef = useRef(null);
  const flyCloneRef = useRef(null);
  const flyDoneRef = useRef(null);
  const busyFlyRef = useRef(false);
  const [fly, setFly] = useState(null);
  const [hideAgent, setHideAgent] = useState(false);
  const [hideTarget, setHideTarget] = useState(false);

  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
    localStorage.setItem("flame-dark", dark ? "1" : "0");
  }, [dark]);

  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth({ ok: false }));
    api.investigations().then(setInvestigations).catch(() => {}).finally(() => setInvLoading(false));
    api.scenarios().then(setScenarios).catch(() => {});
    api.agents().then(setAgents).catch(() => {});
  }, []);

  useEffect(() => {
    localStorage.setItem("flame-threads", JSON.stringify(threads.slice(0, 30)));
  }, [threads]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, pane]);

  useEffect(() => {
    const ta = taRef.current;
    if (ta) { ta.style.height = "auto"; ta.style.height = Math.min(ta.scrollHeight, 140) + "px"; }
  }, [input]);

  const reducedMotion = () => typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const flySeq = (fromEl, toEl, onDone) => {
    if (!fromEl || !toEl || reducedMotion()) { onDone && onDone(); return; }
    const a = fromEl.getBoundingClientRect();
    const b = toEl.getBoundingClientRect();
    if (a.width < 1 || b.width < 1) { onDone && onDone(); return; }
    flyDoneRef.current = onDone;
    setFly({
      a: { left: a.left, top: a.top, width: a.width, height: a.height },
      b: { left: b.left, top: b.top, width: b.width, height: b.height },
      key: Date.now(),
    });
  };

  useEffect(() => {
    if (!fly || !flyCloneRef.current) return;
    const el = flyCloneRef.current;
    const dx = fly.b.left - fly.a.left;
    const dy = fly.b.top - fly.a.top;
    const sc = fly.b.width / fly.a.width;
    const anim = el.animate(
      [
        { transform: "translate(0px, 0px) scale(1)", opacity: 1 },
        { transform: `translate(${dx}px, ${dy}px) scale(${sc})`, opacity: 1 },
      ],
      { duration: 520, easing: "cubic-bezier(.22,1,.36,1)", fill: "forwards" }
    );
    anim.finished.catch(() => {}).then(() => {
      const cb = flyDoneRef.current;
      flyDoneRef.current = null;
      setFly(null);
      cb && cb();
    });
  }, [fly]);

  const openProfile = () => {
    if (pane === "profile") { closeProfile(); return; }
    const wasOpen = !!pane;
    setPane("profile");
    setHideTarget(true);
    if (reducedMotion() || busyFlyRef.current) { setHideAgent(true); setHideTarget(false); return; }
    busyFlyRef.current = true;
    setTimeout(() => {
      setHideAgent(true);
      flySeq(sourceAvatarRef.current, targetAvatarRef.current, () => {
        busyFlyRef.current = false;
        setHideTarget(false);
      });
    }, wasOpen ? 40 : 390);
  };

  const closeProfile = () => {
    if (reducedMotion() || busyFlyRef.current) {
      setPane(null); setHideAgent(false); setHideTarget(false);
      return;
    }
    busyFlyRef.current = true;
    setHideTarget(true);
    flySeq(targetAvatarRef.current, sourceAvatarRef.current, () => {
      busyFlyRef.current = false;
      setHideAgent(false);
      setHideTarget(false);
      setPane(null);
    });
  };

  const openPaneTab = (tab) => {
    setHideAgent(false);
    setHideTarget(false);
    setPane(tab);
  };

  const closePane = () => {
    if (pane === "profile") closeProfile();
    else setPane(null);
  };

  const persistThread = useCallback((msgs) => {
    if (!msgs.length) return;
    setThreads((prev) => {
      const first = msgs.find((m) => m.role === "user");
      const title = (first?.text || "Cuộc trò chuyện").slice(0, 60);
      const id = threadId || `t${Date.now()}`;
      if (!threadId) setThreadId(id);
      const others = prev.filter((t) => t.id !== id);
      return [{ id, title, messages: msgs, ts: Date.now() }, ...others];
    });
  }, [threadId]);

  const send = async (raw) => {
    const q = (raw ?? input).trim();
    if (!q || sending) return;
    setInput("");
    setSending(true);
    const acts = [];
    let acc = "";
    const base = [...messages, { role: "user", text: q }, { role: "assistant", text: "", loading: true, streaming: true, activity: acts }];
    setMessages(base);
    const idx = base.length - 1;
    const patch = (p) => setMessages((cur) => cur.map((m, i) => (i === idx ? { ...m, ...p } : m)));
    const enrichPromise = buildEnrichment(q).catch(() => ({}));
    let streamFigures = [];
    let streamTables = [];
    const finish = async (finalText, figs, tables) => {
      const enrich = await enrichPromise;
      const figureBlocks = (figs || []).map((f) => ({ type: "figure", src: f.src, alt: f.alt || "", caption: f.caption || "" }));
      const done = [...base.slice(0, -1), {
        role: "assistant", text: finalText, activity: acts,
        blocks: [...figureBlocks, ...(enrich.blocks || [])],
        tables: tables || [],
        artifact: enrich.artifact ?? null,
      }];
      setMessages(done);
      persistThread(done);
    };
    try {
      await api.askStream(q, sessionRef.current, {
        onTrace: (s) => { acts.push(s); patch({ activity: [...acts] }); },
        onDelta: (t) => { acc += t; patch({ text: acc, loading: false }); },
        onMeta: (m) => {
          if (m?.sessionId) sessionRef.current = m.sessionId;
          if (Array.isArray(m?.figures)) streamFigures = m.figures;
          if (Array.isArray(m?.tables)) streamTables = m.tables;
        },
      });
      await finish(acc, streamFigures, streamTables);
    } catch (e) {
      if (acc) {
        await finish(acc, streamFigures, streamTables);
      } else {
        try {
          const data = await api.ask(q);
          await finish(data.answer || "", data.figures, data.tables);
        } catch {
          const done = [...base.slice(0, -1), {
            role: "assistant", error: true,
            text: "Không kết nối được tới trợ lý GraphRAG trên máy chủ (API không phản hồi). Dữ liệu thư viện, so sánh và báo cáo vẫn mở được ở panel bên phải; phần chat sẽ hoạt động lại khi backend trực tuyến.",
          }];
          setMessages(done);
          persistThread(done);
        }
      }
    }
    setSending(false);
  };

  const newChat = () => { setMessages([]); setThreadId(null); setDrawer(false); sessionRef.current = null; };
  const openThread = (t) => { setMessages(t.messages || []); setThreadId(t.id); setDrawer(false); };
  const clearThreads = () => { setThreads([]); newChat(); };

  const onMic = () => {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) return;
    const rec = new SR();
    rec.lang = "vi-VN";
    rec.onresult = (e) => setInput((v) => (v + " " + e.results[0][0].transcript).trim());
    rec.start();
  };
  const micSupported = typeof window !== "undefined" && (window.SpeechRecognition || window.webkitSpeechRecognition);

  const railBtn = (key, Icon, label, onClick) => {
    const active = pane === key || (key === "chat" && (!pane || pane === "profile"));
    return (
      <button
        key={key} title={label} aria-label={label} onClick={onClick}
        className={`grid h-10 w-10 place-items-center rounded-xl transition-colors ${active ? "bg-zinc-900/[0.07] text-foreground dark:bg-white/15" : "text-zinc-500 hover:bg-zinc-900/[0.05] hover:text-foreground dark:text-zinc-400 dark:hover:bg-white/10"}`}
      >
        <Icon className="h-5 w-5" strokeWidth={1.8} />
      </button>
    );
  };

  const Meta = pane ? PANE_META[pane] : null;
  const PaneIcon = Meta?.icon;

  return (
    <div className="flex h-full overflow-hidden bg-background">
      {/* ——— Rail điều hướng ——— */}
      <nav aria-label="Không gian làm việc" className="z-20 hidden w-16 shrink-0 flex-col items-center gap-1.5 border-r bg-background py-4 sm:flex">
        <button title="Tìm trong hội thoại" aria-label="Tìm trong hội thoại" onClick={() => setDrawer(true)}
          className="grid h-10 w-10 place-items-center rounded-xl text-zinc-500 transition-colors hover:bg-zinc-900/[0.05] hover:text-foreground dark:text-zinc-400 dark:hover:bg-white/10">
          <Search className="h-5 w-5" strokeWidth={1.8} />
        </button>
        {railBtn("chat", MessageCircle, "Trò chuyện", () => (pane === "profile" ? closeProfile() : setPane(null)))}
        {railBtn("data", LineChart, "Dữ liệu NASA", () => openPaneTab("data"))}
        {railBtn("compare", Columns2, "So sánh", () => openPaneTab("compare"))}
        {railBtn("report", FileText, "Báo cáo", () => openPaneTab("report"))}
        <div className="flex-1" />
        <span title={health?.ok ? "API trực tuyến" : "API ngoại tuyến"} className={`mb-1 h-2 w-2 rounded-full ${health?.ok ? "bg-emerald-500" : "bg-zinc-300"}`} />
        <button title="Cài đặt" aria-label="Cài đặt" onClick={() => setSettings(true)}
          className="grid h-10 w-10 place-items-center rounded-xl text-zinc-500 transition-colors hover:bg-zinc-900/[0.05] hover:text-foreground dark:text-zinc-400 dark:hover:bg-white/10">
          <Settings className="h-5 w-5" strokeWidth={1.8} />
        </button>
        <button title="Lịch sử chat" aria-label="Lịch sử chat" onClick={() => setDrawer(true)}
          className="grid h-10 w-10 place-items-center rounded-xl text-zinc-500 transition-colors hover:bg-zinc-900/[0.05] hover:text-foreground dark:text-zinc-400 dark:hover:bg-white/10">
          <MessagesSquare className="h-5 w-5" strokeWidth={1.8} />
        </button>
      </nav>

      {/* ——— Cột chính ——— */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="relative flex h-[72px] shrink-0 items-center border-b px-4 sm:px-6">
          <button onClick={() => setDrawer(true)} className="flex items-center gap-2 text-sm font-medium text-zinc-600 transition-colors hover:text-foreground dark:text-zinc-300">
            <MessageCircle className="h-[18px] w-[18px]" /> Trò chuyện <ChevronDown className="h-3.5 w-3.5 opacity-60" />
          </button>
          <button
            onClick={openProfile}
            aria-expanded={pane === "profile"}
            title="Hồ sơ Flame"
            style={{ opacity: hideAgent ? 0 : 1 }}
            className="absolute left-1/2 flex -translate-x-1/2 flex-col items-center transition-opacity duration-200"
          >
            <span
              ref={sourceAvatarRef}
              className="grid h-11 w-11 place-items-center rounded-full border-[3px] border-white text-[21px] leading-none text-[#2b313b] shadow-[0_0_0_1px_#e7e5e1,0_2px_10px_rgba(0,0,0,0.11)]"
              style={{ background: "radial-gradient(circle at 35% 22%,#fff 3%,#f1eee6 59%,#dedcd6 100%)" }}
            >✳</span>
            <span className="-mt-0.5 text-[13px] font-semibold leading-tight">Flame</span>
          </button>
          <div className="ml-auto flex items-center gap-1.5">
            <AuthArea />
            <button title="Trò chuyện mới" aria-label="Trò chuyện mới" onClick={newChat}
              className="grid h-9 w-9 place-items-center rounded-full text-zinc-600 transition-colors hover:bg-zinc-900/[0.05] hover:text-foreground dark:text-zinc-300 dark:hover:bg-white/10">
              <Plus className="h-5 w-5" />
            </button>
            <button title={dark ? "Chế độ sáng" : "Chế độ tối"} aria-label="Đổi giao diện sáng/tối" onClick={() => setDark((d) => !d)}
              className="grid h-9 w-9 place-items-center rounded-full border text-zinc-600 transition-colors hover:text-foreground dark:text-zinc-300">
              {dark ? <Sun className="h-[18px] w-[18px]" /> : <Moon className="h-[18px] w-[18px]" />}
            </button>
            <button title="Cài đặt" aria-label="Cài đặt" onClick={() => setSettings(true)}
              className="grid h-9 w-9 place-items-center rounded-full text-zinc-600 transition-colors hover:bg-zinc-900/[0.05] hover:text-foreground dark:text-zinc-300 dark:hover:bg-white/10">
              <Settings className="h-5 w-5" />
            </button>
          </div>
        </header>

        <div className="flex min-h-0 flex-1">
          <main className="flex min-w-0 flex-1 flex-col">
            <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto">
              {messages.length === 0 ? (
                <div className="mx-auto flex min-h-full w-full max-w-[640px] flex-col items-center justify-center px-4 py-10">
                  <div className="eyebrow mb-3">Bây giờ</div>
                  <div className="w-full animate-fade-up rounded-3xl bg-muted/80 px-5 py-4 text-[15px] leading-relaxed dark:bg-white/[0.07]">
                    Chào bạn! 👋<br />
                    Mình là <strong>Flame</strong>, trợ lý giúp bạn khám phá những nghiên cứu về ngọn lửa trong môi trường vi trọng lực.<br /><br />
                    Bạn muốn bắt đầu từ đâu?
                  </div>
                  <p className="mt-3 self-start text-sm text-muted-foreground">Bạn có thể gửi nhiều câu hỏi hoặc mở thư viện NASA PSI ở panel bên phải.</p>
                </div>
              ) : (
                <div className="mx-auto w-full max-w-[780px] space-y-5 px-4 py-6">
                  {messages.map((m, i) => (
                    <ChatMessage key={i} msg={m} onOpenPane={openPaneTab} />
                  ))}
                </div>
              )}
            </div>

            <div className="shrink-0 px-4 pb-4">
              <div className="mx-auto w-full max-w-[780px]">
                {messages.length === 0 ? (
                  <div className="mb-3 flex flex-wrap justify-center gap-2">
                    {PROMPTS.map((p) => (
                      <button key={p.label} onClick={() => send(p.text)}
                        className="rounded-full border bg-background px-4 py-2 text-sm font-medium shadow-sm transition-all hover:-translate-y-px hover:shadow">
                        {p.label} <span className="text-muted-foreground">↗</span>
                      </button>
                    ))}
                  </div>
                ) : null}
                <form
                  onSubmit={(e) => { e.preventDefault(); send(); }}
                  className="flex items-end gap-1.5 rounded-[26px] border bg-card px-2.5 py-2 shadow-[0_10px_36px_rgba(16,24,40,0.10)]"
                >
                  <Textarea
                    ref={taRef} rows={1} value={input} onChange={(e) => setInput(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }}
                    placeholder="Nhắn tin..." aria-label="Nhập tin nhắn"
                    className="max-h-[140px] min-h-[40px] flex-1 resize-none border-0 bg-transparent px-1.5 py-2 shadow-none focus-visible:ring-0"
                  />
                  <button type="button" title="Đính kèm CSV (mở tab Dữ liệu)" aria-label="Đính kèm CSV" onClick={() => openPaneTab("data")}
                    className="grid h-10 w-10 shrink-0 place-items-center rounded-full text-zinc-500 transition-colors hover:bg-accent hover:text-foreground">
                    <Paperclip className="h-5 w-5" />
                  </button>
                  <button type="button" title={micSupported ? "Nhập bằng giọng nói" : "Trình duyệt không hỗ trợ giọng nói"} aria-label="Nhập bằng giọng nói"
                    onClick={onMic} disabled={!micSupported}
                    className="grid h-10 w-10 shrink-0 place-items-center rounded-full text-zinc-500 transition-colors hover:bg-accent hover:text-foreground disabled:opacity-40">
                    <Mic className="h-5 w-5" />
                  </button>
                  <button type="submit" title="Gửi" aria-label="Gửi tin nhắn" disabled={!input.trim() || sending}
                    className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-zinc-900 text-white transition-all hover:bg-zinc-700 disabled:opacity-30 dark:bg-lime dark:text-zinc-950">
                    <ArrowUp className="h-5 w-5" />
                  </button>
                </form>
                <p className="mt-2 text-center text-[11px] text-muted-foreground">
                  {messages.length === 0
                    ? "Bản demo nghiên cứu, không liên kết với Meta Muse. Trợ lý GraphRAG trên dữ liệu NASA PSI thật."
                    : "Trợ lý GraphRAG trên dữ liệu NASA PSI thật (Neo4j Aura). Câu trả lời được tổng hợp từ bằng chứng truy xuất trực tiếp."}
                </p>
              </div>
            </div>
          </main>

          {/* ——— Panel artifact bên phải ——— */}
          {pane && Meta ? (
            <aside className="fixed inset-y-0 right-0 z-40 flex w-[min(580px,96vw)] animate-pane-grow flex-col overflow-hidden border-l bg-background shadow-2xl lg:static lg:z-auto lg:shadow-none">
              <div className="flex items-center gap-3 border-b px-5 py-3.5">
                <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-muted">
                  {Meta.glyph ? <span className="text-[18px] leading-none">{Meta.glyph}</span> : <PaneIcon className="h-[18px] w-[18px]" />}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-semibold">{Meta.title}</span>
                  <span className="block truncate text-xs text-muted-foreground">{Meta.sub}</span>
                </span>
                <button title="Đóng panel" aria-label="Đóng panel" onClick={closePane}
                  className="grid h-8 w-8 place-items-center rounded-full text-zinc-500 transition-colors hover:bg-accent hover:text-foreground">
                  <X className="h-[18px] w-[18px]" />
                </button>
              </div>
              {Meta.h1 ? <h2 className="px-5 pt-4 text-[22px] font-bold leading-tight">{Meta.h1}</h2> : null}
              <div className={`min-h-0 flex-1 overflow-y-auto ${pane === "profile" ? "" : "px-5 py-4"}`}>
                {pane === "profile" ? (
                  <ProfilePane
                    health={health} agents={agents} investigationsCount={investigations.length}
                    onOpenPane={openPaneTab} avatarRef={targetAvatarRef} avatarHidden={hideTarget}
                  />
                ) : null}
                {pane === "data" ? <DataPane investigations={investigations} loading={invLoading} /> : null}
                {pane === "compare" ? <ComparePane investigations={investigations} /> : null}
                {pane === "report" ? <ReportPane scenarios={scenarios} investigations={investigations} /> : null}
              </div>
              {pane !== "profile" ? (
              <div className="grid shrink-0 grid-cols-3 gap-1 border-t p-1.5">
                {Object.entries(PANE_META).filter(([, m]) => m.tab).map(([key, m]) => {
                  const I = m.icon;
                  const active = pane === key;
                  return (
                    <button key={key} onClick={() => openPaneTab(key)}
                      className={`flex items-center justify-center gap-1.5 rounded-lg py-2.5 text-xs transition-colors ${active ? "bg-zinc-900/[0.07] font-semibold dark:bg-white/15" : "text-muted-foreground hover:bg-zinc-900/[0.04] dark:hover:bg-white/10"}`}>
                      <I className="h-4 w-4" /> {m.tab}
                    </button>
                  );
                })}
              </div>
              ) : null}
            </aside>
          ) : null}
        </div>
      </div>

      {/* ——— Avatar bay (clone) ——— */}
      {fly ? (
        <div
          key={fly.key}
          ref={flyCloneRef}
          aria-hidden="true"
          className="pointer-events-none fixed z-[99999] grid place-items-center rounded-full font-extrabold text-[#2c3643]"
          style={{
            left: fly.a.left, top: fly.a.top, width: fly.a.width, height: fly.a.height,
            fontSize: Math.round(fly.a.width * 0.48),
            background: "radial-gradient(circle at 30% 25%,#fff,#e4e7ed)",
            boxShadow: "0 12px 35px rgba(0,0,0,0.20)",
            willChange: "transform",
          }}
        >✳</div>
      ) : null}

      {/* ——— Drawer lịch sử ——— */}
      {drawer ? (
        <div className="fixed inset-0 z-50">
          <div className="absolute inset-0 bg-black/30" onClick={() => setDrawer(false)} />
          <aside className="absolute inset-y-0 left-0 flex w-[300px] animate-drawer-in flex-col border-r bg-background p-4 shadow-2xl">
            <div className="flex items-start justify-between">
              <div>
                <strong className="text-[15px]">Lịch sử trò chuyện</strong>
                <small className="block text-xs text-muted-foreground">Các cuộc trò chuyện trên thiết bị này</small>
              </div>
              <button title="Đóng lịch sử" aria-label="Đóng lịch sử" onClick={() => setDrawer(false)}
                className="grid h-8 w-8 place-items-center rounded-full text-zinc-500 hover:bg-accent hover:text-foreground">
                <X className="h-4 w-4" />
              </button>
            </div>
            <Button variant="dark" className="mt-4 w-full" onClick={newChat}><Plus /> Cuộc trò chuyện mới</Button>
            <div className="eyebrow mb-2 mt-5">Gần đây</div>
            <div className="min-h-0 flex-1 space-y-1 overflow-y-auto">
              {threads.length === 0 ? (
                <p className="text-sm text-muted-foreground">Chưa có cuộc trò chuyện nào được lưu.</p>
              ) : threads.map((t) => (
                <button key={t.id} onClick={() => openThread(t)}
                  className={`block w-full truncate rounded-lg px-3 py-2 text-left text-sm transition-colors hover:bg-accent ${t.id === threadId ? "bg-accent font-medium" : ""}`}>
                  {t.title}
                  <span className="block text-[11px] font-normal text-muted-foreground">
                    {new Date(t.ts).toLocaleString("vi-VN", { hour: "2-digit", minute: "2-digit", day: "2-digit", month: "2-digit" })}
                  </span>
                </button>
              ))}
            </div>
            {threads.length ? (
              <button onClick={clearThreads} className="mt-2 inline-flex items-center gap-1.5 self-start text-xs text-muted-foreground hover:text-red-600">
                <Trash2 className="h-3.5 w-3.5" /> Xóa toàn bộ lịch sử
              </button>
            ) : null}
            <p className="mt-3 border-t pt-3 text-[11px] leading-relaxed text-muted-foreground">
              Trò chuyện được lưu trên trình duyệt của bạn. Dữ liệu nghiên cứu: NASA PSI qua Neo4j GraphRAG.
            </p>
          </aside>
        </div>
      ) : null}

      {/* ——— Cài đặt ——— */}
      <Dialog open={settings} onOpenChange={setSettings}>
        <DialogContent>
          <DialogHeader className="shrink-0">
            <DialogTitle>Cài đặt</DialogTitle>
            <DialogDescription>Trợ lý Flame chạy trên backend GraphRAG của team — khóa API do máy chủ quản lý, không nhập tại đây.</DialogDescription>
          </DialogHeader>
          <div className="min-h-0 space-y-4 overflow-y-auto pr-1 text-sm">
            <div className="flex items-center justify-between gap-3">
              <span className="font-medium">Ngôn ngữ trả lời</span>
              <LanguagePicker />
            </div>
            <div className="flex items-center justify-between gap-3">
              <span className="font-medium">Chế độ trợ lý</span>
              <Badge variant={health?.ok ? "default" : "secondary"}>{health?.ok ? "GraphRAG · trực tuyến" : "Ngoại tuyến"}</Badge>
            </div>
            <div className="flex items-center justify-between gap-3">
              <span className="font-medium">Cơ sở dữ liệu Neo4j</span>
              <span className="text-muted-foreground">{health ? (health.neo4j ? "Đã kết nối" : "Chưa kết nối") : "Đang kiểm tra…"}</span>
            </div>
            <div>
              <div className="mb-1.5 font-medium">Đội agent phía máy chủ ({agents.length})</div>
              <ul className="space-y-1.5">
                {agents.map((a) => (
                  <li key={a.id} className="rounded-lg bg-muted/60 px-3 py-2">
                    <span className="font-mono text-xs font-semibold">{a.id}</span>
                    <span className="block text-xs text-muted-foreground">{a.role}</span>
                  </li>
                ))}
              </ul>
            </div>
            <div>
              <label htmlFor="aiKey" className="mb-1.5 block font-medium">Khóa API</label>
              <input id="aiKey" disabled placeholder="•••••••• (máy chủ quản lý)" className="h-9 w-full rounded-md border border-input bg-muted/50 px-3 text-sm" />
              <p className="mt-1 text-xs text-muted-foreground">Không nhập khóa cá nhân vào bản demo công khai.</p>
            </div>
          </div>
        </DialogContent>
      </Dialog>
      {clerkEnabled && <AuthSync />}
      {clerkEnabled && <AuthGate />}
    </div>
  );
}
