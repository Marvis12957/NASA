import React, { useState } from "react";
import { Sparkles, Download, Copy, Check } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { api, psiUrl } from "@/lib/api";

export default function ReportPane({ scenarios, investigations }) {
  const [scenId, setScenId] = useState("iss");
  const [brief, setBrief] = useState(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const scen = scenarios.find((s) => s.id === scenId) || scenarios[0];

  const generate = async () => {
    if (!scen) return;
    setBusy(true);
    let insight = "";
    try {
      insight = await api.ask(`For the scenario "${scen.label}", which PSI investigations matter most for spacecraft fire safety, and what should a crew or designer take away? Answer concisely with the investigation names and PSI ids.`);
    } catch {
      insight = "Không gọi được trợ lý GraphRAG lúc này — phần nhận định để trống thay vì tự dựng nội dung.";
    }
    const priorities = investigations.slice(0, 5);
    setBrief({ scen, insight, priorities });
    setBusy(false);
  };

  const briefText = () => {
    if (!brief) return "";
    return [
      `BÁO CÁO FLAME IN FREEFALL — ${brief.scen.label}`,
      "",
      "BỐI CẢNH NHIỆM VỤ",
      brief.scen.caveat || "",
      "",
      "NGUỒN ƯU TIÊN",
      ...brief.priorities.map((p) => `- ${p.acronym || p.name} (${p.id}): ${p.reason || p.blurb || ""} — ${psiUrl(p.id)}`),
      "",
      "NHẬN ĐỊNH KHOA HỌC (trợ lý GraphRAG trên dữ liệu PSI)",
      brief.insight,
    ].join("\n");
  };

  const download = () => {
    const esc = (t) => String(t || "").replace(/&/g, "&amp;").replace(/</g, "&lt;");
    const html = `<!doctype html><html lang="vi"><head><meta charset="utf-8"><title>Báo cáo Flame in Freefall — ${esc(brief.scen.label)}</title><style>body{font-family:Georgia,serif;max-width:760px;margin:40px auto;padding:0 20px;line-height:1.65;color:#1b1b1b}h1{font-size:26px}h2{font-size:18px;margin-top:28px}.eyebrow{font:600 10px monospace;letter-spacing:.15em;color:#64748b;text-transform:uppercase}a{color:#0754ce}</style></head><body><h1>Báo cáo nghiên cứu — ${esc(brief.scen.label)}</h1><p class="eyebrow">Flame in Freefall · NASA Space Apps 2026 · Nguồn: NASA PSI / Neo4j GraphRAG</p><h2>Bối cảnh nhiệm vụ</h2><p>${esc(brief.scen.caveat)}</p><h2>Nguồn ưu tiên</h2>${brief.priorities.map((p) => `<p><strong>${esc(p.acronym || p.name)} (${esc(p.id)})</strong> — ${esc(p.reason || p.blurb || "")} <a href="${psiUrl(p.id)}">Nguồn NASA</a></p>`).join("")}<h2>Nhận định khoa học</h2><p style="white-space:pre-wrap">${esc(brief.insight)}</p></body></html>`;
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([html], { type: "text/html" }));
    a.download = "flame-brief.html";
    a.click();
    URL.revokeObjectURL(a.href);
  };

  const copy = async () => {
    try { await navigator.clipboard.writeText(briefText()); setCopied(true); setTimeout(() => setCopied(false), 1800); } catch {}
  };

  const Section = ({ eyebrow, title, children }) => (
    <section className="panel animate-fade-up p-5">
      <span className="inline-block rounded-full bg-muted px-2.5 py-1 font-mono text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">{eyebrow}</span>
      <h3 className="mt-2.5 text-xl font-bold">{title}</h3>
      <div className="mt-1.5 text-sm leading-relaxed text-muted-foreground">{children}</div>
    </section>
  );

  return (
    <div className="space-y-4">
      <section className="panel p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <label htmlFor="scenSel" className="text-sm font-semibold">Kịch bản cần đánh giá</label>
          <select
            id="scenSel" value={scen?.id || ""} onChange={(e) => setScenId(e.target.value)}
            className="h-10 min-w-[220px] rounded-xl border border-input bg-background px-3 text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-ring"
          >
            {scenarios.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
          </select>
        </div>
        <div className="mt-3.5 flex flex-wrap gap-2">
          <Button variant="dark" onClick={generate} disabled={busy || !scen}>
            <Sparkles /> {busy ? "Đang tạo…" : "Tạo báo cáo"}
          </Button>
          <Button variant="outline" onClick={download} disabled={!brief}><Download /> Tải báo cáo .html</Button>
          <Button variant="outline" onClick={copy} disabled={!brief}>{copied ? <Check /> : <Copy />} {copied ? "Đã chép" : "Sao chép"}</Button>
        </div>
      </section>

      {busy ? (
        <div className="space-y-3"><Skeleton className="h-32 w-full" /><Skeleton className="h-32 w-full" /></div>
      ) : brief ? (
        <>
          <Section eyebrow="Kịch bản giao diện" title="Bối cảnh nhiệm vụ">
            <p>{brief.scen.caveat}</p>
          </Section>
          <Section eyebrow="Đối chiếu NASA PSI" title="Nguồn ưu tiên">
            <ul className="space-y-2">
              {brief.priorities.map((p) => (
                <li key={p.id}>
                  <strong className="text-foreground">{p.acronym || p.name} ({p.id})</strong> — {p.reason || p.blurb}{" "}
                  <a className="text-primary underline underline-offset-2" href={psiUrl(p.id)} target="_blank" rel="noreferrer">Nguồn NASA ↗</a>
                </li>
              ))}
            </ul>
          </Section>
          <Section eyebrow="Suy luận định tính" title="Nhận định khoa học">
            <p className="whitespace-pre-wrap">{brief.insight}</p>
          </Section>
        </>
      ) : (
        <p className="py-4 text-center text-sm text-muted-foreground">Chọn kịch bản rồi nhấn “Tạo báo cáo” — báo cáo được lắp từ dữ liệu PSI thật và câu trả lời của trợ lý GraphRAG.</p>
      )}
    </div>
  );
}
