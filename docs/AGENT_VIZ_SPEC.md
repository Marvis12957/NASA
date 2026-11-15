# ĐẶC TẢ — Agent Trực Quan Hoá Thí Nghiệm "FlameViz"

| | |
|---|---|
| **Dự án** | Flame in Freefall — NASA Space Apps Challenge 2026 |
| **Phiên bản** | 1.0 — 15/11/2026 |
| **Nguồn dữ liệu** | Neo4j Aura (GraphRAG) — kiểm kê trực tiếp 15/11/2026: **20.259 node · 73.854 quan hệ** |
| **Lớp giao diện** | OpenUI (generative UI theo catalog component định nghĩa sẵn) |
| **Backend hiện có** | FastAPI tại `/workspace/backend` (Neo4j Aura, `POST /api/ask`, `/api/investigations`, `/api/scenarios`, `/api/agents`, `/health`) |

---

## 1. Mục tiêu và ánh xạ yêu cầu đề bài

Agent biến kho dữ liệu cháy vi trọng lực (NASA PSI) thành **trải nghiệm trực quan tương tác**: người dùng hỏi bằng ngôn ngữ tự nhiên, agent truy vấn đồ thị tri thức và **lắp ráp một giao diện** (thẻ, biểu đồ, đồ thị quan hệ, bảng so sánh, panel bằng chứng) thay vì trả về một đoạn văn bản.

| Yêu cầu đề bài | Chức năng đáp ứng |
|---|---|
| (1) Tìm investigation liên quan nhất đến an toàn cháy tàu vũ trụ | F1 Explorer + xếp hạng, F11 Briefing theo kịch bản |
| (2) Trích xuất dữ liệu phục vụ predictive modeling | F7 Bản đồ bao phủ, F8 Trích xuất dataset (CSV) |
| (3) Làm nổi bật nghiên cứu cho cảm biến phát hiện cháy & hệ thống dập cháy | F9 Ma trận cảm biến & dập cháy, F2 Hồ sơ, F4 Đồ thị quan hệ |

**Người dùng đích:** giám khảo Space Apps, nhà nghiên cứu an toàn cháy, thành viên team trình bày demo.

## 2. Nguyên tắc thiết kế (bắt buộc)

1. **Agent là điều phối viên UI, không phải chatbot.** Đầu ra chuẩn là một *UI Spec* (JSON, mục 7) gồm các block component; văn bản chỉ là phụ trợ.
2. **Catalog đóng, không sinh HTML tự do.** Agent chỉ được chọn component trong catalog (mục 5) và điền dữ liệu đúng schema. OpenUI render từ spec — layout không bao giờ vỡ.
3. **Mọi con số phải truy vết được.** Mỗi stat/biểu đồ/khẳng định đều gắn `evidence` (chunk id + trang + tài liệu) mở được ở EvidencePanel (F10).
4. **Trung thực khi thiếu dữ liệu.** Trường trống → component hiển thị trạng thái "Chưa có dữ liệu đã trích xuất" + nút "Tìm trong văn bản gốc" (truy vấn chunk). **Tuyệt đối không nội suy, không bịa số, không dùng số minh hoạ.**
5. **Ưu tiên dữ liệu cấu trúc, fallback về chunk.** Thứ tự tin cậy: trường cấu trúc của Investigation → quan hệ đồ thị → tổng hợp Community → tìm kiếm vector trên Chunk.
6. **Song ngữ có kiểm soát:** nhãn UI tiếng Việt, tên riêng/thuật ngữ/dữ liệu gốc giữ nguyên tiếng Anh (FLEX, Saffire-II, flame spread…).

## 3. Bản đồ dữ liệu đầu vào (đã kiểm chứng)

### 3.1 Bốn tầng dữ liệu

| Tầng | Nội dung | Quy mô |
|---|---|---|
| T1 · Tài liệu gốc | `Document` → `Chunk` (có `page`, `pages_span`, `figures`, `source`, `embedding` — 100% đã nhúng, vector index `chunk_embedding`) | 450 doc · 6.060 chunk · 43 nguồn PSI/web |
| T2 · Thực thể tri thức | `Hardware` 5.511 · `Phenomenon` 2.638 · `Sensor` 2.345 · `Fuel` 1.767 · `Location` 748 · `Suppressant` 103 — đều có `label`, `aliases`, `pages`, `source_chunks` | 13.372 node |
| T3 · Tổng hợp GraphRAG | `Community` 3 cấp (L0: 227, L1: 183, L2: 180), mỗi node có `title`, `summary`, `size`, `psiIds` | 590 node |
| T4 · Danh mục | `Investigation` 25 · `Family` 7 · `Scenario` 6 · `Context` 6 · `Role` 3 | 47 node |

Quan hệ chính: `MENTIONS` 30.568 · `IN_COMMUNITY` 10.039 · `USED_IN` 9.615 · `STUDIED` 7.390 · `PART_OF` 6.060 · `IN_DOCUMENT` 3.803 · `RELATED_TO` 2.873 · `MEASURED_WITH` 2.762 · `PARENT_COMMUNITY` 410 · `TESTED_FUEL` 100 · `RELEVANT_TO` 100 · `IN_FAMILY` 25 · `USED_HARDWARE` 25.

### 3.2 Độ phủ trường của Investigation (n = 25) — ràng buộc thiết kế

| Trường | Phủ | Hệ quả cho UI |
|---|---|---|
| `summary`, `acronym`, `blurb`, `relevance`, `scores`, `aliases` | 25/25 (100%) | Luôn hiển thị được; `scores` dùng cho xếp hạng F1 |
| `doi`, `sources` | 24/25 (96%) | Hiển thị, chấp nhận 1 hồ sơ thiếu |
| `runCount` | 20/25 (80%) | Stat hiển thị kèm trạng thái thiếu |
| `fuels` | 18/25 (72%) | Thiếu → bổ sung từ quan hệ `TESTED_FUEL`/chunk |
| `measurements`, `sampleForm` | 11/25 (44%) | Không dựng bảng định lượng chỉ từ field này |
| `oxygenMin`, `oxygenMax`, `oxygenNote` | 9/25 (36%) | GaugeO2 phải có empty state chuẩn (F2) |

### 3.3 Cảnh báo chất lượng dữ liệu (phải xử lý ở tầng Data Shaper)

- **Thực thể chưa chuẩn hoá hoàn toàn:** tồn tại node trùng do hoa/thường và biến thể tên (vd `soot` 320 lượt nhắc và `Soot` 240 lượt là 2 node riêng; `CIR` có nhiều biến thể tên dài). → Khi tổng hợp theo thực thể, Data Shaper phải **gộp theo `aliases` + dạng chuẩn hoá (lowercase, bỏ tiền tố `xxx:` và hậu tố `@PSI-xx`)**, hoặc cộng dồn lượt nhắc của các biến thể.
- **Một số Sensor/Suppressant mang nhãn là id thô** (vd `sensor:dusttrak@PSI-47`). → Hiển thị bằng nhãn đã làm sạch; giữ id gốc trong `evidence`.
- **Phân bố chunk lệch theo nguồn:** ACME_common 1.251 chunk, PSI-102 (SAME) 610 chunk + 156 document, `web:flex` 504. → Mọi xếp hạng theo lượt nhắc/tìm kiếm vector phải **chuẩn hoá theo nguồn** (chia cho tổng chunk của nguồn hoặc cap mỗi nguồn) để tránh thiên lệch ACME/SAME.
- **Suppressant mỏng** (103 node, lượt nhắc thấp): chức năng dập cháy (F9) phải ghép với Phenomenon `extinction` (485 lượt) và chunk, không dựa riêng vào nhãn Suppressant.

## 4. Kiến trúc

```
Câu hỏi / thao tác UI
        │
┌───────▼────────┐   intent + entities + filters
│ Intent Router  │──────────────────────────────┐
└───────┬────────┘                              │
        │                              ┌────────▼─────────┐
        │                              │  State Store     │ bộ nhớ phiên: investigation
        │                              │  (client)        │ đang chọn, bộ lọc, lịch sử
        │                              └────────▲─────────┘
┌───────▼────────┐   Cypher / vector   │ sự kiện click (cross-filter)
│ Query Planner  │─────────────────────┘
└───────┬────────┘
        │ raw rows
┌───────▼────────┐   gộp aliases, chuẩn hoá nguồn, gắn evidence,
│  Data Shaper   │   đánh dấu trường thiếu (missing flags)
└───────┬────────┘
        │ shaped data
┌───────▼────────┐   chọn component theo loại dữ liệu + intent,
│  UI Composer   │   sinh UI Spec JSON (mục 7) + follow-up chips
└───────┬────────┘
        │ UI Spec
┌───────▼────────┐
│ OpenUI Renderer│  render theo catalog (mục 5), style NASA light
└────────────────┘
```

**Quy tắc chọn tầng dữ liệu theo intent:** câu hỏi "cái nào/liệt kê/so sánh" → T4+T2 (Cypher); "tại sao/như thế nào/tổng quan" → T3 (Community summary) + T1; "số liệu cụ thể/trang nào nói" → T1 (vector + chunk); "liên quan tới X" → duyệt đồ thị T2 theo quan hệ.

**Giới hạn cứng:** NodeGraph tối đa 60 node / 120 cạnh mỗi lần render (cắt theo lượt nhắc giảm dần); EvidencePanel tối đa 5 chunk/lần; mọi truy vấn Cypher có `LIMIT`; timeout truy vấn 10 giây → trả block lỗi thân thiện, không treo UI.

## 5. Catalog component OpenUI

Agent chỉ được dùng các component sau. `data` phải đúng schema; trường nào thiếu trong DB thì để `null` và đặt cờ `missing: true` — renderer tự vẽ empty state.

| Component | Dữ liệu vào (rút gọn) | Nguồn | Dùng ở |
|---|---|---|---|
| `StatCard` | `{label, value, sub?, tone}` | tổng hợp | F1, F7, F11 |
| `InvestigationCard` | `{id, name, acronym, family, relevance, fuels[], runCount, doi}` | T4 `Investigation` | F1, F11 |
| `ProfileHeader` | `{id, name, acronym, summary, family, facility?, doi, sources[]}` | T4 | F2 |
| `GaugeO2` | `{min, max, note, missing}` | `oxygenMin/Max/Note` | F2, F3 |
| `ChipList` | `{title, items[{label, count?}]}` | `fuels`, hardware, aliases | F2, F3 |
| `FlowSteps` | `{steps[{title, text}]}` | trường `flow` của Investigation (fallback: tóm tắt từ chunk) | F2 |
| `NodeGraph` | `{nodes[{id, label, type, weight}], edges[{from, to, type}]}` | duyệt T2 | F4 |
| `HeatMap` | `{x[], y[], cells[{x, y, v}], caption}` | tổng hợp MENTIONS | F7, F9 |
| `BarChart` | `{series[{label, value}], unit}` | tổng hợp (chunk/doc theo nguồn…) | F7 |
| `CompareTable` | `{columns[2..3 investigation], rows[{field, values[], highlightDiff}]}` | T4 + T2 | F3 |
| `SensorMatrix` | `{sensors[], investigations[], cells}` | `MEASURED_WITH` | F9 |
| `TopicDrilldown` | `{levels, communities[{id, title, summary, size}], breadcrumb}` | T3 `Community` | F5 |
| `EvidencePanel` | `{chunks[{id, text, page, documentTitle, source, figures[]}]}` | T1 `Chunk` | F10 (gắn kèm mọi chức năng) |
| `DataTable` + `ExportCSV` | `{columns[], rows[][], warnings[]}` | T4 + T2 | F8 |
| `ScenarioBriefing` | layout ghép: header + top investigations + rủi ro + cảm biến khuyến nghị | T4 `Scenario` + `RELEVANT_TO` | F11 |
| `SourceBadge` | `{kind: 'field' \| 'graph' \| 'chunk' \| 'community', ref}` | Data Shaper | mọi stat/con số |
| `MissingData` | `{field, action: 'search_chunks'}` | Data Shaper | mọi component thiếu dữ liệu |

**Style binding (renderer):** theme NASA light hiện hành của team — nền trắng, ink `#1B1B1B`, primary NASA Blue `#0B3D91`, accent Action Blue `#105BD8`, NASA Red `#FC3D21` chỉ cho cảnh báo/điểm nhấn, font Public Sans. Màu theo loại node trong NodeGraph: Investigation = NASA Blue, Hardware = Action Blue, Fuel = cam cháy, Phenomenon = đỏ NASA, Sensor = teal, Location = xám.

## 6. Đặc tả chức năng

Quy ước: **P0** = MVP phải có trước deadline 23:59 ngày 15/11/2026 · **P1** = mở rộng sau khi P0 ổn định.

### F1 · Explorer — khám phá & xếp hạng investigation · P0

- **Mô tả:** lưới toàn bộ 25 investigation, lọc theo Family (7), nhiên liệu, cơ sở (Location), và xếp hạng theo điểm liên quan an toàn cháy (`scores`/`relevance`, đủ 25/25).
- **Truy vấn mẫu:**
  ```cypher
  MATCH (i:Investigation)-[:IN_FAMILY]->(f:Family)
  WHERE ($family IS NULL OR f.id = $family)
  RETURN i.id, i.label, i.acronym, i.scores, i.fuels, i.runCount, f.label
  ORDER BY i.relevance DESC LIMIT 25
  ```
- **UI:** `InvestigationCard` × n + `StatCard` tổng quan (25 hồ sơ · 7 family · 450 tài liệu · 6.060 chunk).
- **Nghiệm thu:** lọc Family cho ra đúng số hồ sơ theo kiểm kê (CIR+ACME khí: 6, Phân tích/mô hình: 5, MSG/SPICE: 4, MDCA giọt lỏng: 3, Aerosol SAME+DAFT: 3, Cygnus: 3, USML-1: 1).

### F2 · Hồ sơ thí nghiệm (Profile) · P0

- **Mô tả:** trang trực quan cho 1 investigation: định danh, tóm tắt, điều kiện thí nghiệm, thiết bị, nhiên liệu, hiện tượng, bằng chứng.
- **Block layout chuẩn:** `ProfileHeader` → hàng `StatCard` (số lần chạy, số tài liệu, số chunk, DOI) → `GaugeO2` + `ChipList`(fuels) → `FlowSteps` → `ChipList`(hardware) → `EvidencePanel` (3 chunk tiêu biểu).
- **Xử lý thiếu:** thiếu `oxygenMin/Max` (16/25 hồ sơ) → `GaugeO2` chuyển `MissingData` kèm nút tìm trong chunk theo mẫu "oxygen / O₂ / %" trong các chunk của hồ sơ; tìm thấy số → hiển thị dạng *trích xuất từ văn bản* với `SourceBadge kind='chunk'`, không giả vờ là field cấu trúc.
- **Nghiệm thu:** mở PSI-99 (Saffire-II) hiển thị đúng: family Cygnus, nhiên liệu Silicone/SIBAL/PMMA/Nomex (từ chunk/fuels), khoảng oxy 21,5–22,1% kèm nguồn.

### F3 · So sánh đối đầu (Compare) · P1

- **Mô tả:** chọn 2–3 investigation → `CompareTable`: hàng = trường (family, facility, fuels, khoảng O₂, runCount, hardware chính, hiện tượng chính, số chunk bằng chứng), tô sáng ô khác biệt; ô thiếu ghi "—" + cờ missing.
- **Nghiệm thu:** so sánh PSI-99 vs PSI-69 (FLEX) vs PSI-102 (SAME) không lỗi khi một bên thiếu O₂.

### F4 · Đồ thị quan hệ (NodeGraph) · P0

- **Mô tả:** từ 1 investigation (hoặc 1 thực thể), mở đồ thị 1–2 bước theo `USED_IN`, `STUDIED`, `MEASURED_WITH`, `TESTED_FUEL`, `RELATED_TO`; cạnh `MENTIONS` chỉ dùng để tính trọng số node, không vẽ hết (30.568 cạnh sẽ rối).
- **Truy vấn mẫu:**
  ```cypher
  MATCH (i:Investigation {id: $id})-[r:USED_IN|STUDIED|MEASURED_WITH|TESTED_FUEL|USED_HARDWARE]-(e)
  RETURN e, type(r) AS rel, count(*) AS w ORDER BY w DESC LIMIT 60
  ```
- **Tương tác:** click node → mở F2 (nếu là investigation) hoặc mở `EvidencePanel` các chunk nhắc tới node đó; click cạnh → giải thích quan hệ 1 câu (sinh từ template, không LLM bịa).
- **Nghiệm thu:** đồ thị quanh PSI-102 (SAME) có DustTrak/P-Trak và hiện tượng smoke/soot; render ≤ 60 node, tải < 2 giây.

### F5 · Bản đồ chủ đề (TopicDrilldown) · P1

- **Mô tả:** duyệt `Community` theo 3 cấp bằng `PARENT_COMMUNITY`; mỗi cấp hiển thị `TopicDrilldown` (title + summary có sẵn, `size` = độ lớn); chọn 1 community → danh sách investigation liên quan (`psiIds`) + chunk tiêu biểu.
- **Nghiệm thu:** từ cấp 2 (180 community) khoan xuống được cấp 0 và quay lui bằng breadcrumb không mất trạng thái.

### F6 · Liên kết chéo & trạng thái phiên (Cross-filter) · P0 (cơ chế, không phải màn hình)

- Mọi component phát sự kiện chọn (`select fuel`, `select investigation`, `select sensor`…); State Store cập nhật bộ lọc chung; các block đang hiển thị **tự truy vấn lại** theo bộ lọc. Ví dụ: chọn Fuel "PMMA" ở HeatMap → Explorer chỉ còn hồ sơ dùng PMMA, NodeGraph thu về các node liên quan PMMA.
- Composer phải giữ **bộ lọc đang áp dụng** hiển thị dạng chip xoá được ở đầu trang.

### F7 · Bản đồ bao phủ dữ liệu (Coverage) · P1

- **Mô tả:** trực quan "dữ liệu đang có gì, thưa ở đâu": `BarChart` chunk theo nguồn (43 nguồn, top: ACME_common 1.251, PSI-102 610, web:flex 504), `HeatMap` Fuel × Phenomenon (MENTIONS đã chuẩn hoá nguồn), biểu đồ độ phủ trường của Investigation (theo bảng 3.2).
- **Mục đích:** trả lời yêu cầu đề bài (2) — cho thấy vùng nào đủ dữ liệu để mô hình hoá, vùng nào chưa.
- **Nghiệm thu:** heatmap không bị ACME/SAME lấn át sau chuẩn hoá; ghi rõ phương pháp chuẩn hoá ở caption.

### F8 · Trích xuất dataset cho predictive modeling · P1

- **Mô tả:** agent dựng `DataTable` phẳng: 1 dòng/investigation, cột = acronym, family, fuels, oxygenMin, oxygenMax, runCount, sampleForm, measurements, số document, số chunk; nút `ExportCSV` tải file; cột nào phủ < 50% thì gắn `warnings` ở đầu bảng.
- **Nghiệm thu:** CSV tải về mở được, 25 dòng, ô trống để trống (không điền 0/bịa).

### F9 · Ma trận cảm biến & dập cháy · P1

- **Mô tả:** `SensorMatrix`: hàng = Sensor (đã làm sạch nhãn), cột = Investigation, ô = có/không + số lượt (từ `MEASURED_WITH`, 2.762 quan hệ). Khối dập cháy: danh sách Suppressant + Phenomenon `extinction` liên quan + chunk bằng chứng, kèm ghi chú minh bạch "dữ liệu dập cháy trong PSI còn mỏng (103 node)".
- **Ánh xạ đề bài:** yêu cầu (3). Điểm nhấn demo: SAME (PSI-102) ↔ DustTrak/P-Trak cho phát hiện khói/aerosol.
- **Nghiệm thu:** ma trận có PSI-47 (DustTrak 45 lượt, P-Trak 43 lượt theo MENTIONS) hiển thị đúng sau khi gộp biến thể nhãn cảm biến.

### F10 · Trình xem bằng chứng (EvidencePanel) · P0

- **Mô tả:** panel trượt/mở rộng hiển thị chunk gốc: trích đoạn văn bản, `page`/`pages_span`, tên tài liệu (`Document.title`), đường dẫn nguồn (`source`), hình (`figures`) nếu có. Mọi `SourceBadge` trên UI đều mở được panel này đúng chunk.
- **Truy vấn mẫu:**
  ```cypher
  MATCH (c:Chunk)-[:PART_OF]->(d:Document)
  WHERE c.id IN $chunkIds
  RETURN c.id, c.text, c.page, d.title, d.source LIMIT 5
  ```
- **Nghiệm thu:** từ Profile PSI-99, mở bằng chứng thấy chunk ghi khoảng oxy 21,5–22,1% kèm đúng trang/tài liệu.

### F11 · Briefing theo kịch bản (Scenario Briefing) · P0

- **Mô tả:** chọn 1 trong 6 `Scenario` (ISS, Lunar Gateway, Moon Base, EVA Suit…) → agent lắp `ScenarioBriefing`: mô tả kịch bản (`Context.summary` nếu có), top investigation liên quan (qua `RELEVANT_TO`, 100 quan hệ, + `scores`), rủi ro cháy chính (Phenomenon nổi bật), cảm biến khuyến nghị (Sensor liên quan), khoảng trống dữ liệu (cảnh báo trung thực).
- **Nghiệm thu:** briefing ISS ưu tiên SAME/FLEX/Saffire đúng theo scores; mọi khuyến nghị đều có evidence mở được.

### F12 · Hỏi–đáp dự phòng (QA fallback) · P0 (mỏng)

- Câu hỏi không khớp intent trực quan nào → trả lời văn bản ngắn (tối đa ~120 từ) **kèm** `EvidencePanel` và 2–3 follow-up chip gợi ý chuyển sang view trực quan ("Xem đồ thị quan hệ của FLEX", "So sánh SAME với BASS-II"…). Không bao giờ trả text trần không nguồn.

## 7. Giao thức UI Spec (định dạng agent trả về)

Mọi phản hồi của agent là một JSON `UISpec`. Frontend/OpenUI chỉ render theo spec này.

```json
{
  "version": "1.0",
  "intent": "profile",
  "title": "Hồ sơ thí nghiệm — Saffire-II (PSI-99)",
  "filters": [{ "kind": "investigation", "id": "inv:PSI-99", "label": "Saffire-II" }],
  "blocks": [
    { "component": "ProfileHeader",
      "data": { "id": "inv:PSI-99", "name": "Saffire-II", "acronym": "Saffire",
                "family": "Cygnus vehicle after it left the ISS",
                "summary": "…", "doi": "…", "sources": ["…"] } },
    { "component": "GaugeO2",
      "data": { "min": 21.5, "max": 22.1, "note": "Measured oxygen concentration",
                "missing": false },
      "evidence": [{ "chunkId": "…", "page": 3, "document": "…" }] },
    { "component": "ChipList",
      "data": { "title": "Nhiên liệu / vật liệu",
                "items": [{ "label": "PMMA" }, { "label": "Nomex" },
                          { "label": "Silicone" }, { "label": "Cotton-Fiberglass (SIBAL)" }] } },
    { "component": "NodeGraph", "data": { "nodes": ["…"], "edges": ["…"] } },
    { "component": "EvidencePanel", "data": { "chunks": ["…"] } }
  ],
  "followups": ["So sánh với FLEX (PSI-69)", "Cảm biến nào đo được khói trong Saffire?", "Xem đồ thị quan hệ"]
}
```

Quy ước: `blocks` xếp theo thứ tự hiển thị; block lỗi dữ liệu bị thay bằng `MissingData` thay vì làm hỏng cả trang; `followups` tối đa 3 chip.

## 8. Bảng định tuyến intent

| Ý định người dùng (ví dụ) | Intent | Chức năng | Block chính |
|---|---|---|---|
| "Có những nghiên cứu nào về… / liệt kê…" | `explore` | F1 | InvestigationCard, StatCard |
| "Cho xem hồ sơ / chi tiết PSI-99" | `profile` | F2 | ProfileHeader, GaugeO2, FlowSteps |
| "So sánh SAME và BASS-II" | `compare` | F3 | CompareTable |
| "X liên quan tới gì / thiết bị nào…" | `graph` | F4 | NodeGraph |
| "Tổng quan chủ đề cháy trên ISS" | `topic` | F5 | TopicDrilldown |
| "Dữ liệu phủ tới đâu / đủ để train model không" | `coverage` | F7 (+F8) | BarChart, HeatMap, DataTable |
| "Xuất bảng dữ liệu" | `extract` | F8 | DataTable, ExportCSV |
| "Cảm biến nào phát hiện khói tốt / dập cháy ra sao" | `sensor` | F9 | SensorMatrix, EvidencePanel |
| "Trang nào nói vậy / nguồn đâu" | `evidence` | F10 | EvidencePanel |
| "Nếu là trên Lunar Gateway thì sao" | `scenario` | F11 | ScenarioBriefing |
| Còn lại | `qa` | F12 | văn bản ngắn + EvidencePanel |

## 9. API cần có (mở rộng backend hiện tại)

Giữ nguyên các endpoint cũ; bổ sung:

| Endpoint | Vào | Ra |
|---|---|---|
| `POST /api/viz` | `{question, state}` (state = bộ lọc + investigation đang chọn) | `UISpec` (mục 7) |
| `GET /api/viz/profile/{psi_id}` | — | `UISpec` hồ sơ (F2) — cho phép mở link trực tiếp không qua chat |
| `GET /api/viz/graph/{node_id}?depth=1` | — | dữ liệu `NodeGraph` đã cắt ≤ 60 node |
| `GET /api/viz/coverage` | — | dữ liệu F7 (cache 1 giờ, dữ liệu tĩnh theo bản trích xuất) |
| `GET /api/viz/extract.csv` | — | file CSV của F8 |

Yêu cầu: mọi endpoint trả kèm `evidence` refs; lỗi Neo4j → HTTP 200 với block `MissingData` + thông điệp, tránh sập UI khi demo (chỉ lỗi hệ thống thật mới 5xx).

## 10. Phi chức năng

- **Hiệu năng:** trang hồ sơ < 2 giây; NodeGraph < 2 giây; tổng hợp nặng (HeatMap, Coverage) tiền tính & cache ở backend (`backend/var/`), không chạy Cypher tổng hợp toàn đồ thị theo từng câu hỏi.
- **Độ tin cậy demo:** có chế độ "snapshot" — cache UISpec cho ~10 câu hỏi demo chuẩn, phòng khi Neo4j/tunnel chập chờn trong lúc thuyết trình.
- **Truy cập:** tương phản ≥ 4.5:1 (chuẩn NASA WDS), mọi biểu đồ có bảng dữ liệu thay thế (screen-reader + chính là DataTable của F8).
- **Ngôn ngữ:** UI tiếng Việt; dữ liệu/trích dẫn giữ nguyên văn tiếng Anh, không dịch số liệu.

## 11. Lộ trình triển khai

**Đợt 1 — trước deadline 23:59 ngày 15/11/2026 (P0):** catalog lõi (StatCard, InvestigationCard, ProfileHeader, GaugeO2 + MissingData, ChipList, NodeGraph, EvidencePanel, ScenarioBriefing) · F1, F2, F4, F6 (cơ chế lọc cơ bản), F10, F11, F12 · endpoint `POST /api/viz` + snapshot demo.

**Đợt 2 (P1):** F3 Compare · F5 TopicDrilldown · F7 Coverage · F8 Extract CSV · F9 SensorMatrix · FlowSteps đầy đủ · chuẩn hoá thực thể (gộp aliases) ở Data Shaper.

## 12. Những điều agent KHÔNG được làm

1. Không bịa số liệu, khoảng đo, hay "số minh hoạ" — thiếu thì nói thiếu (xem cảnh báo về ảnh dashboard cũ: số chi tiết trong ảnh AI là số minh hoạ, không được đưa vào UI này).
2. Không gán sai cơ sở thí nghiệm (vd Saffire bay trên tàu Cygnus, không phải CIR — lỗi đã gặp ở ảnh dashboard 5).
3. Không dịch/đổi tên riêng và mã PSI; không đổi đơn vị đo.
4. Không trả lời định lượng chỉ từ Community summary — số liệu phải từ field cấu trúc hoặc chunk, có evidence.
5. Không vẽ đồ thị toàn bộ 20.259 node — luôn cắt theo ngữ cảnh câu hỏi và giới hạn ở mục 4.

---

*Đặc tả này dựa trên kiểm kê database trực tiếp ngày 15/11/2026 (20.259 node, 73.854 quan hệ) và schema backend hiện tại. Khi dữ liệu trích xuất thay đổi, cập nhật lại mục 3 trước khi sửa chức năng.*
