import React from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/**
 * Hiển thị câu trả lời của agent đúng định dạng Markdown (GFM):
 * tiêu đề, đậm/nghiêng, danh sách, bảng, trích dẫn, code — styled theo theme chat.
 */
const components = {
  h1: ({ children }) => <h1 className="mb-2 mt-4 text-xl font-bold leading-snug first:mt-0">{children}</h1>,
  h2: ({ children }) => <h2 className="mb-2 mt-4 text-lg font-bold leading-snug first:mt-0">{children}</h2>,
  h3: ({ children }) => <h3 className="mb-1.5 mt-3.5 text-[15px] font-semibold leading-snug first:mt-0">{children}</h3>,
  h4: ({ children }) => <h4 className="mb-1 mt-3 text-sm font-semibold first:mt-0">{children}</h4>,
  p: ({ children }) => <p className="my-2 text-[15px] leading-relaxed first:mt-0 last:mb-0">{children}</p>,
  ul: ({ children }) => <ul className="my-2 list-disc space-y-1 pl-5 text-[15px] leading-relaxed first:mt-0 last:mb-0">{children}</ul>,
  ol: ({ children }) => <ol className="my-2 list-decimal space-y-1 pl-5 text-[15px] leading-relaxed first:mt-0 last:mb-0">{children}</ol>,
  li: ({ children }) => <li className="leading-relaxed">{children}</li>,
  strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
  em: ({ children }) => <em>{children}</em>,
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noreferrer" className="font-medium text-primary underline decoration-primary/40 underline-offset-2 hover:decoration-primary">
      {children}
    </a>
  ),
  blockquote: ({ children }) => (
    <blockquote className="my-2 border-l-[3px] border-zinc-300 pl-3 text-muted-foreground dark:border-white/20 [&>p]:my-1">
      {children}
    </blockquote>
  ),
  hr: () => <hr className="my-4 border-zinc-200 dark:border-white/10" />,
  table: ({ children }) => (
    <div className="my-3 overflow-x-auto rounded-xl border bg-white dark:bg-white/5">
      <table className="w-full border-collapse text-sm">{children}</table>
    </div>
  ),
  thead: ({ children }) => <thead className="bg-muted/70">{children}</thead>,
  th: ({ children }) => <th className="border-b px-3 py-2 text-left font-semibold">{children}</th>,
  td: ({ children }) => <td className="border-b px-3 py-2 align-top leading-relaxed">{children}</td>,
  tr: ({ children }) => <tr className="last:[&>td]:border-b-0">{children}</tr>,
  code: ({ className, children }) => {
    const isBlock = (className || "").includes("language-");
    if (isBlock) return <code className={`${className} font-mono text-[13px]`}>{children}</code>;
    return <code className="rounded-md bg-zinc-900/[0.07] px-1.5 py-0.5 font-mono text-[13px] dark:bg-white/10">{children}</code>;
  },
  pre: ({ children }) => (
    <pre className="my-3 overflow-x-auto rounded-xl bg-zinc-900 p-3.5 text-zinc-100 dark:bg-black/50">{children}</pre>
  ),
  img: ({ src, alt }) => (
    <img src={src} alt={alt || "Hình từ tài liệu"} className="my-3 max-h-80 max-w-full rounded-xl border bg-zinc-50 object-contain dark:bg-black/20" />
  ),
};

export default function MarkdownAnswer({ text }) {
  if (!text) return null;
  return (
    <div className="markdown-answer">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {text}
      </ReactMarkdown>
    </div>
  );
}
