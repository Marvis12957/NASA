import React, { useMemo, useRef, useState } from "react";
import { ArrowLeft, ExternalLink, Search, Upload, BarChart3 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { api, psiUrl, invTitle } from "@/lib/api";

function DetailView({ id, onBack }) {
  const [d, setD] = useState(null);
  const [err, setErr] = useState("");
  React.useEffect(() => {
    let live = true;
    setD(null); setErr("");
    api.investigation(id).then((x) => live && setD(x)).catch((e) => live && setErr(e.message));
    return () => { live = false; };
  }, [id]);
  if (err) return <div className="space-y-3"><Button variant="ghost" size="sm" onClick={onBack}><ArrowLeft /> Quay lại</Button><p className="text-sm text-red-600">Không tải được hồ sơ: {err}</p></div>;
  if (!d) return <div className="space-y-3"><Skeleton className="h-6 w-2/3" /><Skeleton className="h-24 w-full" /><Skeleton className="h-24 w-full" /></div>;
  const rows = [
    ["Họ thí nghiệm", d.family],
    ["Nhiên liệu / vật liệu", (d.fuels || []).join(", ")],
    ["Oxy", d.oxygen || (d.oxygenMin != null ? `${d.oxygenMin}–${d.oxygenMax}%` : null)],
    ["Số lần chạy", d.runCount != null ? String(d.runCount) : null],
    ["Luồng khí", d.flow],
    ["DOI", d.doi],
  ].filter((r) => r[1]);
  return (
    <div className="animate-fade-up space-y-4">
      <Button variant="ghost" size="sm" onClick={onBack}><ArrowLeft /> Thư viện nghiên cứu</Button>
      <div>
        <div className="flex flex-wrap gap-1.5">
          <Badge variant="mono">NASA PSI</Badge><Badge variant="mono">{d.id}</Badge>
          {d.acronym ? <Badge variant="mono">{d.acronym}</Badge> : null}
        </div>
        <h3 className="mt-2 text-xl font-bold leading-snug">{invTitle(d)}</h3>
        <p className="mt-1.5 text-sm leading-relaxed text-muted-foreground">{d.blurb || d.reason}</p>
      </div>
      <div className="overflow-hidden rounded-xl border">
        {rows.map(([k, v], i) => (
          <div key={i} className="flex gap-3 border-b px-3.5 py-2.5 text-sm last:border-0">
            <span className="w-36 shrink-0 font-medium text-primary">{k}</span>
            <span className="min-w-0 flex-1">{v}</span>
          </div>
        ))}
      </div>
      {d.summary ? (
        <div>
          <div className="eyebrow mb-1.5">Tóm tắt khoa học (nguyên văn PSI)</div>
          <p className="text-sm leading-relaxed text-muted-foreground">{d.summary}</p>
        </div>
      ) : null}
      {(d.peers || []).length ? (
        <p className="text-sm text-muted-foreground">
          Hồ sơ cùng dòng: {d.peers.map((p) => `${p.name} (${p.id})`).join(" · ")}
        </p>
      ) : null}
      <Button variant="outline" size="sm" asChild>
        <a href={psiUrl(d.id)} target="_blank" rel="noreferrer">Nguồn NASA <ExternalLink /></a>
      </Button>
    </div>
  );
}

function CsvTool() {
  const [rows, setRows] = useState(null);
  const [cols, setCols] = useState([]);
  const [x, setX] = useState("");
  const [y, setY] = useState("");
  const [status, setStatus] = useState("Chưa có tệp. Nạp CSV để vẽ biểu đồ từ dữ liệu của bạn.");
  const fileRef = useRef(null);

  const onFile = (f) => {
    if (!f) return;
    f.text().then((text) => {
      const lines = text.trim().split(/\r?\n/);
      const head = lines[0].split(",").map((c) => c.trim());
      const data = lines.slice(1).map((ln) => ln.split(",").map((c) => c.trim())).filter((r) => r.length === head.length);
      setCols(head); setRows(data);
      setX(head[0]); setY(head.find((c, i) => i > 0 && data.some((r) => !isNaN(parseFloat(r[i])))) || head[1] || "");
      setStatus(`Đã nạp ${data.length} dòng · ${head.length} cột từ ${f.name}`);
    }).catch(() => setStatus("Không đọc được tệp CSV."));
  };

  const chart = useMemo(() => {
    if (!rows || !x || !y) return null;
    const xi = cols.indexOf(x), yi = cols.indexOf(y);
    const pts = rows.map((r) => ({ lb: r[xi], v: parseFloat(r[yi]) })).filter((p) => !isNaN(p.v)).slice(0, 24);
    if (!pts.length) return null;
    const max = Math.max(...pts.map((p) => p.v), 1);
    return { pts, max };
  }, [rows, cols, x, y]);

  return (
    <section className="panel">
      <div className="panel-head">
        <h3 className="flex items-center gap-2 text-sm font-semibold"><BarChart3 className="h-4 w-4" /> CSV của bạn</h3>
        <Button size="sm" variant="outline" onClick={() => fileRef.current?.click()}><Upload /> Nạp CSV</Button>
        <input ref={fileRef} type="file" accept=".csv,text/csv" className="hidden" onChange={(e) => onFile(e.target.files?.[0])} />
      </div>
      <div className="space-y-3 p-4">
        <p className="text-xs text-muted-foreground">{status}</p>
        {rows ? (
          <>
            <div className="flex flex-wrap gap-2">
              <label className="flex items-center gap-2 text-xs font-medium">Trục X
                <select value={x} onChange={(e) => setX(e.target.value)} className="h-9 rounded-lg border border-input bg-background px-2 text-sm">
                  {cols.map((c) => <option key={c}>{c}</option>)}
                </select>
              </label>
              <label className="flex items-center gap-2 text-xs font-medium">Trục Y
                <select value={y} onChange={(e) => setY(e.target.value)} className="h-9 rounded-lg border border-input bg-background px-2 text-sm">
                  {cols.map((c) => <option key={c}>{c}</option>)}
                </select>
              </label>
              <span className="self-center font-mono text-[11px] text-muted-foreground">{rows.length} dòng</span>
            </div>
            {chart ? (
              <div className="space-y-1.5">
                {chart.pts.map((p, i) => (
                  <div key={i} className="flex items-center gap-2 text-[11px]">
                    <span className="w-24 shrink-0 truncate text-muted-foreground" title={p.lb}>{p.lb}</span>
                    <span className="h-3 flex-1 overflow-hidden rounded-full bg-muted">
                      <span className="block h-full rounded-full bg-primary" style={{ width: `${(p.v / chart.max) * 100}%` }} />
                    </span>
                    <span className="w-12 shrink-0 text-right font-mono">{p.v}</span>
                  </div>
                ))}
              </div>
            ) : <p className="text-xs text-muted-foreground">Chọn cột Y có giá trị số để vẽ biểu đồ.</p>}
          </>
        ) : null}
      </div>
    </section>
  );
}

export default function DataPane({ investigations, loading }) {
  const [q, setQ] = useState("");
  const [fam, setFam] = useState("all");
  const [detailId, setDetailId] = useState(null);

  const families = useMemo(() => [...new Set(investigations.map((i) => i.family).filter(Boolean))], [investigations]);
  const list = useMemo(() => investigations.filter((i) => {
    if (fam !== "all" && i.family !== fam) return false;
    if (!q) return true;
    const hay = `${i.id} ${i.name} ${i.acronym || ""} ${(i.fuels || []).join(" ")} ${i.blurb || ""}`.toLowerCase();
    return hay.includes(q.toLowerCase());
  }), [investigations, q, fam]);

  if (detailId) return <DetailView id={detailId} onBack={() => setDetailId(null)} />;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2">
        {["✦ Hồ sơ PSI xác thực", "✦ Đường dẫn NASA/DOI", "◉ Không dựng số liệu đo", "⊕ Hỗ trợ CSV người dùng nhập"].map((t, i) => (
          <span key={i} className="rounded-full border bg-muted/50 px-3 py-1.5 text-xs font-medium text-muted-foreground">{t}</span>
        ))}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-semibold">NASA investigations</span>
        <div className="relative min-w-[180px] flex-1">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Tìm BASS, FLEX, khói, vật liệu…" className="pl-8" />
        </div>
        <select value={fam} onChange={(e) => setFam(e.target.value)} className="h-9 max-w-[190px] rounded-md border border-input bg-background px-2 text-sm shadow-sm">
          <option value="all">Tất cả</option>
          {families.map((f) => <option key={f} value={f}>{f}</option>)}
        </select>
      </div>

      {loading ? (
        <div className="space-y-3"><Skeleton className="h-36 w-full" /><Skeleton className="h-36 w-full" /></div>
      ) : list.length === 0 ? (
        <p className="py-8 text-center text-sm text-muted-foreground">Không có hồ sơ khớp bộ lọc. Thử từ khóa khác.</p>
      ) : (
        <div className="space-y-3">
          {list.map((inv) => (
            <article key={inv.id} className="panel animate-fade-up p-5">
              <div className="flex flex-wrap gap-1.5">
                <Badge variant="mono">● NASA PSI</Badge>
                <Badge variant="mono">{inv.id}</Badge>
                {inv.familyId ? <Badge variant="mono">{inv.familyId}</Badge> : null}
              </div>
              <h3 className="mt-2 text-lg font-bold leading-snug">{invTitle(inv)}</h3>
              <p className="mt-1.5 line-clamp-3 text-sm leading-relaxed text-muted-foreground">{inv.blurb || inv.reason}</p>
              <div className="mt-3 flex items-center justify-between gap-3">
                <span className="min-w-0 truncate text-xs text-muted-foreground">{inv.family}</span>
                <span className="flex shrink-0 gap-2">
                  <Button size="sm" variant="outline" onClick={() => setDetailId(inv.id)}>Chi tiết</Button>
                  <Button size="sm" variant="outline" asChild>
                    <a href={psiUrl(inv.id)} target="_blank" rel="noreferrer">Nguồn NASA <ExternalLink /></a>
                  </Button>
                </span>
              </div>
            </article>
          ))}
        </div>
      )}

      <CsvTool />
    </div>
  );
}
