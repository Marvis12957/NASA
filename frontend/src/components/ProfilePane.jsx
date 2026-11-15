import React, { useState } from "react";
import { BookOpen, Columns2, FileText, Compass } from "lucide-react";

/**
 * Panel Hồ sơ agent — port đúng bản thiết kế gốc (mwAgentPanel):
 * hero avatar lớn (đích bay của avatar topbar), pill trạng thái,
 * 3 tab Hoạt động / Nguồn / Giới thiệu.
 */
export default function ProfilePane({ health, agents, investigationsCount, onOpenPane, avatarRef, avatarHidden }) {
  const [tab, setTab] = useState("activity");
  const online = !!health?.ok;

  const quick = [
    { tab: "data", icon: BookOpen, emoji: "📚", title: "Thư viện NASA", desc: `${investigationsCount || 25} hồ sơ nghiên cứu PSI có nguồn` },
    { tab: "compare", icon: Columns2, emoji: "◫", title: "So sánh thí nghiệm", desc: "Đối chiếu các hồ sơ nghiên cứu với nhau" },
    { tab: "report", icon: FileText, emoji: "☷", title: "Tạo báo cáo", desc: "Tổng hợp nhận định an toàn có ghi nguồn" },
  ];

  const TabBtn = ({ id, children }) => (
    <button
      role="tab" aria-selected={tab === id} onClick={() => setTab(id)}
      className={`flex-1 rounded-[11px] px-2 py-2.5 text-xs transition-all ${tab === id ? "bg-card font-semibold shadow-[0_2px_8px_rgba(0,0,0,0.08)]" : "text-muted-foreground hover:text-foreground"}`}
    >
      {children}
    </button>
  );

  const ListItem = ({ emoji, title, desc, onClick }) => (
    <button onClick={onClick} className="flex w-full items-center gap-3 rounded-[14px] border bg-card p-3 text-left transition-all hover:bg-accent hover:translate-x-[3px]">
      <span className="grid h-9 w-9 shrink-0 place-items-center rounded-[11px] bg-muted text-[17px]">{emoji}</span>
      <span className="min-w-0">
        <b className="block text-[13px] font-semibold">{title}</b>
        <small className="mt-0.5 block text-[11px] text-muted-foreground">{desc}</small>
      </span>
    </button>
  );

  return (
    <div className="animate-fade-up px-6 pb-8 pt-8">
      <div className="pb-7 text-center">
        <div
          ref={avatarRef}
          aria-hidden="true"
          style={{ opacity: avatarHidden ? 0 : 1 }}
          className="mx-auto mb-[18px] grid h-[118px] w-[118px] place-items-center rounded-full border-[5px] border-background text-[48px] text-[#2b313b] shadow-[0_0_0_1px_hsl(var(--border)),0_6px_26px_rgba(0,0,0,0.09)]"
        >
          <span className="grid h-full w-full place-items-center rounded-full" style={{ background: "linear-gradient(160deg,#f7f7f9,#dce3ed)" }}>✳</span>
        </div>
        <h2 className="text-2xl font-bold tracking-tight">Flame</h2>
        <div className="mt-3 inline-flex items-center gap-[7px] rounded-full bg-muted px-[13px] py-[7px] text-xs">
          <span className={`h-[7px] w-[7px] rounded-full ${online ? "bg-green-500 shadow-[0_0_0_3px_rgba(34,197,94,0.14)]" : "bg-zinc-400"}`} />
          {online ? "Sẵn sàng hỗ trợ" : "Mất kết nối API"}
        </div>
        <p className="mx-auto mt-3 max-w-[320px] text-[13px] leading-relaxed text-muted-foreground">
          Trợ lý nghiên cứu về ngọn lửa và an toàn cháy trong môi trường vi trọng lực.
        </p>
      </div>

      <div className="mb-6 flex gap-1 rounded-[15px] border bg-muted/60 p-1" role="tablist" aria-label="Thông tin trợ lý">
        <TabBtn id="activity">Hoạt động</TabBtn>
        <TabBtn id="sources">Nguồn</TabBtn>
        <TabBtn id="about">Giới thiệu</TabBtn>
      </div>

      {tab === "activity" ? (
        <div>
          <h3 className="mb-3 text-xs font-bold uppercase tracking-wide text-muted-foreground">Truy cập nhanh</h3>
          <div className="flex flex-col gap-2">
            {quick.map((q) => (
              <ListItem key={q.tab} emoji={q.emoji} title={q.title} desc={q.desc} onClick={() => onOpenPane(q.tab)} />
            ))}
          </div>
          <div className="mt-[18px] rounded-[14px] border bg-muted/50 p-[15px] text-xs leading-[1.8] text-muted-foreground">
            <b className="text-foreground">Lưu ý:</b> Flame trả lời dựa trên bằng chứng truy xuất từ cơ sở tri thức NASA PSI (Neo4j GraphRAG). Khi dữ liệu chưa đủ, agent sẽ nói rõ thay vì suy đoán.
          </div>
        </div>
      ) : null}

      {tab === "sources" ? (
        <div>
          <h3 className="mb-3 text-xs font-bold uppercase tracking-wide text-muted-foreground">Hồ sơ NASA PSI</h3>
          <div className="flex flex-col gap-2">
            <ListItem emoji="◈" title="BASS-II, FLEX, Saffire-I, SAME-R, FGM" desc="Mở thư viện để đọc nguồn và DOI" onClick={() => onOpenPane("data")} />
          </div>
          <div className="mt-[18px] rounded-[14px] border bg-muted/50 p-[15px] text-xs leading-[1.8] text-muted-foreground">
            Dữ liệu được truy vấn trực tiếp từ Neo4j GraphRAG của team (20.259 node · 73.854 quan hệ, kiểm kê 15/11/2026). Số đo chi tiết nằm trong tài liệu PSI gốc — mở thư viện để đọc nguồn và DOI.
          </div>
        </div>
      ) : null}

      {tab === "about" ? (
        <div>
          <div className="rounded-[14px] border bg-muted/50 p-[15px] text-xs leading-[1.8] text-muted-foreground">
            <b className="text-foreground">Flame / Freefall Lab</b><br />
            Ứng dụng hackathon độc lập phục vụ NASA Space Apps Challenge 2026. Không phải sản phẩm chính thức của NASA hoặc Muse. Chat chạy trên trợ lý GraphRAG của team với dữ liệu NASA PSI thật.
          </div>
          {agents?.length ? (
            <>
              <h3 className="mb-3 mt-5 text-xs font-bold uppercase tracking-wide text-muted-foreground">Đội agent phía máy chủ</h3>
              <div className="flex flex-col gap-2">
                {agents.map((a) => (
                  <div key={a.id} className="flex items-center gap-3 rounded-[14px] border bg-card p-3">
                    <span className="grid h-9 w-9 shrink-0 place-items-center rounded-[11px] bg-muted"><Compass className="h-[17px] w-[17px]" /></span>
                    <span className="min-w-0">
                      <b className="block font-mono text-[13px] font-semibold">{a.id}</b>
                      <small className="mt-0.5 block text-[11px] text-muted-foreground">{a.role}</small>
                    </span>
                  </div>
                ))}
              </div>
            </>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
