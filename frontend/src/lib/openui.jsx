import React from "react";
import { z } from "zod";
import { Renderer, createLibrary, defineComponent } from "@openuidev/react-lang";
import { openuiLibrary } from "@openuidev/react-ui";

/**
 * OpenUI — hai lớp render:
 * 1) Thân câu trả lời: dùng openuiLibrary chính thức của OpenUI (MarkDownRenderer).
 * 2) Khối dữ liệu giàu (stat / biểu đồ / bảng / thẻ investigation): thư viện
 *    component riêng "FlameViz" khai báo bằng defineComponent + zod schema,
 *    đúng catalog trong đặc tả AGENT_VIZ_SPEC.md.
 */

const s = (v) => JSON.stringify(String(v ?? ""));

/* ——— Thư viện FlameViz ——— */

const Root = defineComponent({
  name: "Root",
  description: "Vertical stack container for rich blocks",
  props: z.object({ children: z.array(z.any()) }),
  component: ({ props, renderNode }) => (
    <div className="flex flex-col gap-3">{renderNode(props.children)}</div>
  ),
});

const StatRow = defineComponent({
  name: "StatRow",
  description: "Row of KPI stat cards",
  props: z.object({
    items: z.array(z.object({ label: z.string(), value: z.string(), sub: z.string().optional() })),
  }),
  component: ({ props: { items } }) => (
    <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-4">
      {items.map((it, i) => (
        <div key={i} className="rounded-xl border bg-white px-3 py-2.5 dark:bg-white/5">
          <div className="font-mono text-[9px] font-semibold uppercase tracking-[0.14em] text-zinc-400">{it.label}</div>
          <div className="mt-0.5 truncate text-[15px] font-bold leading-tight" title={it.value}>{it.value}</div>
          {it.sub ? <div className="truncate text-[11px] text-zinc-500" title={it.sub}>{it.sub}</div> : null}
        </div>
      ))}
    </div>
  ),
});

const BarChart = defineComponent({
  name: "BarChart",
  description: "Horizontal bar chart with caption",
  props: z.object({
    title: z.string(),
    labels: z.array(z.string()),
    values: z.array(z.number()),
    caption: z.string().optional(),
  }),
  component: ({ props: { title, labels, values, caption } }) => {
    const max = Math.max(...values, 1);
    return (
      <div className="rounded-xl border bg-white p-3.5 dark:bg-white/5">
        <div className="mb-2.5 text-[13px] font-semibold">{title}</div>
        <div className="space-y-1.5">
          {labels.map((lb, i) => (
            <div key={i} className="flex items-center gap-2 text-[11px]">
              <span className="w-28 shrink-0 truncate text-zinc-500" title={lb}>{lb}</span>
              <span className="h-2.5 flex-1 overflow-hidden rounded-full bg-zinc-100 dark:bg-white/10">
                <span className="block h-full rounded-full bg-[#0754ce]" style={{ width: `${(values[i] / max) * 100}%` }} />
              </span>
              <span className="w-10 shrink-0 text-right font-mono font-medium">{values[i]}</span>
            </div>
          ))}
        </div>
        {caption ? <div className="mt-2 text-[11px] leading-snug text-zinc-400">{caption}</div> : null}
      </div>
    );
  },
});

const FactTable = defineComponent({
  name: "FactTable",
  description: "Key-value fact table",
  props: z.object({
    title: z.string().optional(),
    rows: z.array(z.object({ k: z.string(), v: z.string() })),
  }),
  component: ({ props: { title, rows } }) => (
    <div className="overflow-hidden rounded-xl border bg-white dark:bg-white/5">
      {title ? <div className="border-b px-3.5 py-2.5 text-[13px] font-semibold">{title}</div> : null}
      <dl>
        {rows.map((r, i) => (
          <div key={i} className="flex gap-3 border-b px-3.5 py-2 text-[13px] last:border-0">
            <dt className="w-32 shrink-0 font-medium text-[#0754ce] dark:text-[#9db9ff]">{r.k}</dt>
            <dd className="min-w-0 flex-1 text-zinc-700 dark:text-zinc-200">{r.v}</dd>
          </div>
        ))}
      </dl>
    </div>
  ),
});

const InvCard = defineComponent({
  name: "InvCard",
  description: "Investigation summary card",
  props: z.object({
    psiId: z.string(),
    name: z.string(),
    family: z.string(),
    blurb: z.string(),
    fuels: z.array(z.string()),
  }),
  component: ({ props: { psiId, name, family, blurb, fuels } }) => (
    <div className="rounded-xl border bg-white p-3.5 dark:bg-white/5">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="rounded-full bg-zinc-100 px-2 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wide text-zinc-500 dark:bg-white/10">NASA PSI</span>
        <span className="rounded-full bg-zinc-100 px-2 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wide text-zinc-500 dark:bg-white/10">{psiId}</span>
      </div>
      <div className="mt-1.5 text-[15px] font-bold leading-snug">{name}</div>
      <p className="mt-1 text-[13px] leading-relaxed text-zinc-600 dark:text-zinc-300">{blurb}</p>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {fuels.slice(0, 6).map((f, i) => (
          <span key={i} className="rounded-full border px-2 py-0.5 text-[11px] text-zinc-600 dark:text-zinc-300">{f}</span>
        ))}
      </div>
      <div className="mt-2 text-[11px] text-zinc-400">{family}</div>
    </div>
  ),
});

const CalloutBox = defineComponent({
  name: "CalloutBox",
  description: "Info or warning callout",
  props: z.object({ variant: z.string(), title: z.string(), text: z.string() }),
  component: ({ props: { variant, title, text } }) => (
    <div className={`rounded-xl border p-3.5 text-[13px] leading-relaxed ${variant === "warn" ? "border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-100" : "border-blue-100 bg-blue-50/60 text-zinc-700 dark:border-white/10 dark:bg-white/5 dark:text-zinc-200"}`}>
      <span className="font-semibold">{title} </span>{text}
    </div>
  ),
});

const EvidenceFigure = defineComponent({
  name: "EvidenceFigure",
  description: "Real evidence image retrieved with the answer, with source caption",
  props: z.object({ src: z.string(), alt: z.string(), caption: z.string() }),
  component: ({ props: { src, alt, caption } }) => (
    <figure className="overflow-hidden rounded-xl border bg-white dark:bg-white/5">
      <a href={src} target="_blank" rel="noreferrer" title="Mở ảnh kích thước đầy đủ">
        <img src={src} alt={alt} loading="lazy" className="max-h-[380px] w-full bg-zinc-50 object-contain dark:bg-white/5" />
      </a>
      {caption ? <figcaption className="px-3.5 py-2.5 text-[11px] leading-snug text-zinc-500 dark:text-zinc-400">{caption}</figcaption> : null}
    </figure>
  ),
});

export const flameVizLibrary = createLibrary({
  components: [Root, StatRow, BarChart, FactTable, InvCard, CalloutBox, EvidenceFigure],
  root: "Root",
});

/* ——— Sinh openui-lang ——— */

export function answerToLang(markdown) {
  if (!markdown) return null;
  return `root = Stack([body])\nbody = MarkDownRenderer(${s(markdown)})`;
}

export function blocksToLang(blocks) {
  if (!blocks || !blocks.length) return null;
  const lines = [];
  const refs = [];
  blocks.forEach((b, i) => {
    const v = `b${i}`;
    refs.push(v);
    if (b.type === "stats") lines.push(`${v} = StatRow(${JSON.stringify(b.items)})`);
    else if (b.type === "barchart") lines.push(`${v} = BarChart(${s(b.title)}, ${JSON.stringify(b.labels)}, ${JSON.stringify(b.values)}, ${s(b.caption || "")})`);
    else if (b.type === "facts") lines.push(`${v} = FactTable(${s(b.title || "")}, ${JSON.stringify(b.rows)})`);
    else if (b.type === "invcard") lines.push(`${v} = InvCard(${s(b.psiId)}, ${s(b.name)}, ${s(b.family)}, ${s(b.blurb)}, ${JSON.stringify(b.fuels || [])})`);
    else if (b.type === "callout") lines.push(`${v} = CalloutBox(${s(b.variant || "info")}, ${s(b.title)}, ${s(b.text)})`);
    else if (b.type === "figure") lines.push(`${v} = EvidenceFigure(${s(b.src)}, ${s(b.alt || "")}, ${s(b.caption || "")})`);
    else return;
  });
  if (!refs.length) return null;
  lines.push(`root = Root([${refs.join(", ")}])`);
  return lines.join("\n");
}

/* ——— Component render có biên lỗi ——— */

class Boundary extends React.Component {
  state = { err: false };
  static getDerivedStateFromError() { return { err: true }; }
  componentDidCatch() {}
  render() { return this.state.err ? this.props.fallback : this.props.children; }
}

export function OpenUIAnswer({ markdown }) {
  const lang = React.useMemo(() => answerToLang(markdown), [markdown]);
  if (!lang) return null;
  return (
    <Boundary fallback={<div className="whitespace-pre-wrap text-[15px] leading-relaxed">{markdown}</div>}>
      <div className="openui-surface">
        <Renderer response={lang} library={openuiLibrary} />
      </div>
    </Boundary>
  );
}

export function OpenUIBlocks({ blocks }) {
  const lang = React.useMemo(() => blocksToLang(blocks), [blocks]);
  if (!lang) return null;
  return (
    <Boundary fallback={null}>
      <div className="openui-surface">
        <Renderer response={lang} library={flameVizLibrary} />
      </div>
    </Boundary>
  );
}
