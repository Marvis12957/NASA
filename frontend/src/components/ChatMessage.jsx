import React, { useState } from "react";
import { Copy, Check, Flame, Loader2, ChevronDown } from "lucide-react";
import { OpenUIBlocks } from "@/lib/openui";
import MarkdownAnswer from "@/components/MarkdownAnswer";

const AGENT_LABELS = {
  ranker: "Agent xếp hạng",
  reader: "Agent đọc hồ sơ",
  comparer: "Agent so sánh",
  navigator: "Agent duyệt đồ thị",
  graphrag: "Agent tìm kiếm GraphRAG",
};

const AGENT_DOING = {
  ranker: "Đang xếp hạng hồ sơ…",
  reader: "Đang đọc hồ sơ…",
  comparer: "Đang so sánh…",
  navigator: "Đang duyệt đồ thị tri thức…",
  graphrag: "Đang tìm trong cơ sở tri thức…",
};

function TypingDots() {
  return (
    <div className="flex items-center gap-1.5 py-1" aria-label="Flame đang trả lời">
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="h-2 w-2 rounded-full bg-zinc-400"
          style={{ animation: `typing-dot 1.2s ease-in-out ${i * 0.18}s infinite` }}
        />
      ))}
    </div>
  );
}

/** Bảng CSV thí nghiệm gốc (payload `tables` từ backend): số liệu nguyên
 * bản từ file Experiment Table của hồ sơ, không qua LLM chép lại. */
function TableCard({ table }) {
  if (!table?.columns?.length || !table?.rows?.length) return null;
  return (
    <figure className="mt-3 overflow-hidden rounded-xl border bg-white dark:bg-white/5">
      <figcaption className="border-b bg-zinc-50 px-3 py-2 text-[12px] font-medium text-zinc-600 dark:bg-white/5 dark:text-zinc-300">
        Bảng dữ liệu thí nghiệm · {table.psi} — {table.file}
        <span className="block text-[11px] font-normal text-muted-foreground">
          Hiển thị {table.shownRows}/{table.totalRows} hàng · Nguồn: file CSV gốc trong kho dữ liệu PSI
        </span>
      </figcaption>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-[12.5px]">
          <thead>
            <tr>
              {table.columns.map((c, i) => (
                <th key={i} className="whitespace-nowrap border-b px-2.5 py-1.5 text-left font-semibold">{c}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {table.rows.map((r, i) => (
              <tr key={i} className="odd:bg-zinc-50/60 dark:odd:bg-white/[0.03]">
                {r.map((cell, j) => (
                  <td key={j} className="whitespace-nowrap border-b px-2.5 py-1.5">{cell}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </figure>
  );
}

export default function ChatMessage({ msg, onOpenPane }) {
  const [copied, setCopied] = useState(false);
  const [showActs, setShowActs] = useState(false);

  if (msg.role === "user") {
    return (
      <div className="flex animate-fade-up justify-end">
        <div className="max-w-[78%] whitespace-pre-wrap rounded-3xl bg-[#d9e8fd] px-4 py-3 text-[15px] leading-relaxed text-zinc-900 dark:bg-[#173a75] dark:text-white">
          {msg.text}
        </div>
      </div>
    );
  }

  const copy = async () => {
    try { await navigator.clipboard.writeText(msg.text || ""); setCopied(true); setTimeout(() => setCopied(false), 1600); } catch {}
  };
  const acts = msg.activity || [];
  const streaming = !!msg.streaming;
  const lastAct = acts[acts.length - 1];
  const doingLabel = !lastAct ? "Đang suy nghĩ…" : AGENT_DOING[lastAct.agentId] || "Đang xử lý…";

  return (
    <div className="animate-fade-up">
      <div className="py-1">
        {msg.error ? (
          <p className="text-[15px] leading-relaxed text-red-600 dark:text-red-300">{msg.text}</p>
        ) : (
          <div className="space-y-4">
            {streaming || acts.length ? (
              <div className="text-[13px]">
                {streaming ? (
                  <span className="inline-flex items-center gap-1.5 text-muted-foreground">
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    <span className="animate-pulse">{doingLabel}</span>
                  </span>
                ) : (
                  <>
                    <button
                      type="button"
                      onClick={() => setShowActs((v) => !v)}
                      className="inline-flex items-center gap-1 text-muted-foreground transition-colors hover:text-foreground"
                    >
                      <span>Đã xử lý qua {acts.length} bước</span>
                      <ChevronDown className={`h-3.5 w-3.5 transition-transform ${showActs ? "rotate-180" : ""}`} />
                    </button>
                    {showActs ? (
                      <div className="mt-2 space-y-2">
                        {acts.map((s, i) => (
                          <div key={i} className="leading-snug">
                            <span className="font-medium text-zinc-600 dark:text-zinc-300">{AGENT_LABELS[s.agentId] || s.agentId || "Agent"}</span>
                            {s.tool ? <span className="ml-1.5 font-mono text-[11px] text-muted-foreground">{s.tool}</span> : null}
                            {s.summary ? <span className="block truncate text-muted-foreground" title={s.summary}>{s.summary}</span> : null}
                          </div>
                        ))}
                      </div>
                    ) : null}
                  </>
                )}
              </div>
            ) : null}

            {msg.text ? <MarkdownAnswer text={msg.text} /> : streaming || msg.loading ? <TypingDots /> : null}
            {msg.blocks?.length ? <OpenUIBlocks blocks={msg.blocks} /> : null}
            {msg.tables?.length ? msg.tables.map((t, i) => <TableCard key={i} table={t} />) : null}

            {msg.artifact ? (
              <button
                onClick={() => onOpenPane(msg.artifact.tab)}
                className="flex w-full items-center gap-3 rounded-2xl border bg-white p-4 text-left shadow-sm transition-shadow hover:shadow dark:bg-white/5"
              >
                <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-orange-50 text-lg dark:bg-orange-500/15">
                  <Flame className="h-5 w-5 text-orange-500" />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-semibold">{msg.artifact.title}</span>
                  <span className="block truncate text-xs text-muted-foreground">{msg.artifact.sub}</span>
                </span>
                <span className="text-muted-foreground">↗</span>
              </button>
            ) : null}
          </div>
        )}
      </div>
      {!msg.loading && !streaming ? (
        <button onClick={copy} className="mt-1.5 inline-flex items-center gap-1.5 px-1 text-xs text-muted-foreground transition-colors hover:text-foreground">
          {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
          {copied ? "Đã sao chép" : "Sao chép câu trả lời"}
        </button>
      ) : null}
    </div>
  );
}
