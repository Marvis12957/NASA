import React, { useEffect, useMemo, useState } from "react";
import { ExternalLink } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { api, psiUrl } from "@/lib/api";

const ROWS = [
  ["Hồ sơ", (d) => d.id],
  ["Nguồn NASA", (d) => "link"],
  ["Họ thí nghiệm", (d) => d.family],
  ["Nhiên liệu / vật liệu", (d) => (d.fuels || []).join(", ")],
  ["Oxy", (d) => d.oxygen || (d.oxygenMin != null ? `${d.oxygenMin}–${d.oxygenMax}%` : "")],
  ["Số lần chạy", (d) => (d.runCount != null ? String(d.runCount) : "")],
  ["Luồng khí", (d) => d.flow || ""],
  ["DOI", (d) => d.doi || ""],
  ["Tóm tắt", (d) => d.blurb || d.reason || ""],
];

export default function ComparePane({ investigations }) {
  const ids = useMemo(() => investigations.map((i) => i.id), [investigations]);
  const [sel, setSel] = useState(["PSI-25", "PSI-69", "PSI-101"]);
  const [details, setDetails] = useState({});
  const chosen = sel.filter((x) => x && ids.includes(x)).filter((v, i, a) => a.indexOf(v) === i);

  useEffect(() => {
    chosen.forEach((id) => {
      if (!details[id]) {
        api.investigation(id).then((d) => setDetails((m) => ({ ...m, [id]: d }))).catch(() => {});
      }
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sel.join(","), ids.length]);

  const setSlot = (i, v) => setSel((s) => s.map((x, j) => (j === i ? v : x)));

  return (
    <div className="space-y-4">
      <section className="panel p-4">
        <div className="mb-3 flex items-center justify-between">
          <h3 className="text-sm font-semibold">Chọn tối đa 3 nghiên cứu</h3>
          <Badge variant="mono">NASA PSI records</Badge>
        </div>
        <div className="space-y-2.5">
          {[0, 1, 2].map((i) => (
            <select
              key={i} value={sel[i] || ""} onChange={(e) => setSlot(i, e.target.value)}
              className="h-11 w-full rounded-xl border border-input bg-background px-3 text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-ring"
            >
              <option value="">— Bỏ trống —</option>
              {investigations.map((inv) => (
                <option key={inv.id} value={inv.id}>{inv.id} · {inv.acronym || inv.name}</option>
              ))}
            </select>
          ))}
        </div>
      </section>

      {chosen.length === 0 ? (
        <p className="py-6 text-center text-sm text-muted-foreground">Chọn ít nhất một nghiên cứu để xem bảng so sánh.</p>
      ) : chosen.some((id) => !details[id]) ? (
        <div className="space-y-2"><Skeleton className="h-10 w-full" /><Skeleton className="h-10 w-full" /><Skeleton className="h-10 w-full" /></div>
      ) : (
        <section className="panel animate-fade-up overflow-x-auto p-4">
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="border-b">
                <th className="py-2 pr-3 text-left font-mono text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">Đặc điểm</th>
                {chosen.map((id) => (
                  <th key={id} className="min-w-[130px] px-3 py-2 text-left font-bold">{details[id].acronym || details[id].name}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {ROWS.map(([label, get], ri) => (
                <tr key={ri} className="border-b align-top last:border-0">
                  <td className="py-3 pr-3 font-medium text-primary">{label}</td>
                  {chosen.map((id) => {
                    const v = get(details[id]);
                    return (
                      <td key={id} className="px-3 py-3 leading-relaxed text-muted-foreground">
                        {v === "link" ? (
                          <a className="inline-flex items-center gap-1 font-medium text-foreground underline underline-offset-2" href={psiUrl(id)} target="_blank" rel="noreferrer">
                            Mở PSI <ExternalLink className="h-3.5 w-3.5" />
                          </a>
                        ) : v ? v : "—"}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mt-3 text-xs text-muted-foreground">Ô “—” nghĩa là hồ sơ PSI chưa có trường dữ liệu này trong bản trích xuất — hệ thống không tự điền số thay thế.</p>
        </section>
      )}
    </div>
  );
}
