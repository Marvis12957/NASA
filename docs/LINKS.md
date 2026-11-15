# LINKS — Tài nguyên cần thiết cho team Flame in Freefall

> Tổng hợp 14/11/2026 cho team NASA Space Apps 2026 — challenge **Flame in Freefall**.
> **Deadline nộp bài: 23:59 ngày 15/11/2026.**
> Cách dùng: làm Wow Pack thì đi thẳng xuống **Mục 3**. Mọi dữ liệu PSI đều mở, không cần tài khoản,
> giấy phép **CC0-1.0** — khi trích phải ghi **DOI của investigation** đó.

---

## 1. Challenge và nguồn chính thức

| # | Link | Dùng để |
|---|------|---------|
| 1 | [Trang challenge Flame in Freefall](https://www.spaceappschallenge.org/2026/challenges/flame-in-freefall/) | Đề bài gốc, yêu cầu nộp |
| 2 | [NASA Combustion Science — Glenn](https://www.nasa.gov/glenn/glenn-expertise-space-exploration/physical-sciences-program/combustion-science/) | Tổng quan các investigation, taxonomy chuẩn để đặt tên |
| 3 | [PSI Data Repository: Combustion Science](https://psi.nasa.gov/physci/repo/search?q=combustion) | **Kho dữ liệu chính** — 26 investigation (24 đúng Combustion Science) |
| 4 | [PSI Database Guide (PDF)](https://assets.science.nasa.gov/content/dam/science/bps/bps-general/bps-data_osdr_psi/PSI%20Database%20Guide%201.pdf) | Cách tải file/thư mục từ PSI (PSI **không có API công khai** — tải về cache) |
| 5 | [Researcher's Guide to ISS Combustion Science](https://www.nasa.gov/missions/station/a-researchers-guide-to-combustion-science/) — [PDF trực tiếp 56 trang](https://www.nasa.gov/wp-content/uploads/2016/05/np-2015-10-034-jsc_combustion_in_reduced_gravity-iss-mini-book-011116-508.pdf) | Bối cảnh từng investigation; **Phụ lục trang 52** là bảng phần cứng có cấu trúc (MDCA/ACME/SoFIE/MSG) để trích vào graph |
| 6 | [ScienceCasts: The Power of Studying Combustion on the ISS (video ~3:39, 2018)](https://www.youtube.com/watch?v=W1oXykoz9zg) | Video giới thiệu FLEX/ACME/SoFIE, nhúng được vào trang mở đầu |

## 2. PSI — từng investigation quan trọng (link record gốc)

| PSI | Investigation | DOI / ghi chú | Vai trò trong sản phẩm |
|-----|---------------|---------------|------------------------|
| [PSI-69](https://psi.nasa.gov/physci/repo/data/investigations/PSI-69) | FLEX | DOI 10.60555/mbq8-0451 · bảng **275 dòng** · kho 860 GB | **Wow Pack 4** — chart giọt cháy, tải bảng trực tiếp |
| [PSI-68](https://psi.nasa.gov/physci/repo/data/investigations/PSI-68) | FLEX-2 | — | Bổ sung cho FLEX |
| [PSI-26](https://psi.nasa.gov/physci/repo/data/investigations/PSI-26) | BASS | DOI 10.60555/58h4-dy80 · bảng **123 dòng** · kho 152 GB, nhiều MP4 thô ISS | **Wow Pack 2** — đối chiếu BASS-II |
| [PSI-25](https://psi.nasa.gov/physci/repo/data/investigations/PSI-25) | BASS-II | — | **Wow Pack 2** — bộ ba nghịch lý B16/B20/B19 |
| [PSI-98](https://psi.nasa.gov/physci/repo/data/investigations/PSI-98) | SAFFIRE-I | DOI 10.60555/0t15-1z43 · bảng 5 dòng **có mã Camera 1/2** | **Wow Pack 1** — nối dòng test tới file camera. ⚠️ Metadata trang này bị lẫn thông tin SAME — không trích phần đó |
| [PSI-99](https://psi.nasa.gov/physci/repo/data/investigations/PSI-99) | SAFFIRE-II | — | Wow Pack 1 |
| [PSI-100](https://psi.nasa.gov/physci/repo/data/investigations/PSI-100) | SAFFIRE-III | — | Wow Pack 1 |
| [PSI-102](https://psi.nasa.gov/physci/repo/data/investigations/PSI-102) | SAME | DOI 10.60555/gd4k-8h62 · bảng chỉ **9 dòng** | Trục Detection — dữ liệu mỏng, làm thẻ thông tin |
| [PSI-101](https://psi.nasa.gov/physci/repo/data/investigations/PSI-101) | SAME-R | — | Trục Detection |
| [PSI-20](https://psi.nasa.gov/physci/repo/data/investigations/PSI-20) | ACME BRE | DOI 10.60555/vb93-3y72 · bảng 19 dòng gộp ngày, có sẵn file CSV · kho 1,85 TB | Bẫy cần ghi chú: BRE đốt khí để mô phỏng cháy vật liệu. ⚠️ Có ô dữ liệu lỗi "3400%" O₂ |
| [PSI-47](https://psi.nasa.gov/physci/repo/data/investigations/PSI-47) | DAFT / DAFT-2 | — | Bẫy cần ghi chú: dùng phần cứng SAME nhưng để **đo bụi**, không đo khói |

## 3. Nguồn theo từng Wow Pack (đi thẳng vào việc)

### Pack 1 — SAFFIRE (video lửa thật)
- Record PSI-98/99/100 ở Mục 2. Media gốc trong PSI nằm dạng ZIP khung hình — phải tự chọn/ghép.
- Trang tham khảo cách trình bày video + đo viền lửa: MicroFire Flame Vision — [Saffire-V](https://microfire-atlas.vercel.app/analyze/saffire-v-ribs) · [Saffire-VI](https://microfire-atlas.vercel.app/analyze/saffire-vi-pmma) *(chỉ tham khảo cách làm, số liệu phải trích từ nguồn NASA gốc)*

### Pack 2 — BASS-II (nghịch lý cùng O₂, khác kết cục)
- **Nguồn chính:** BASS-II Summary Report, **NTRS 20210011385**, Bảng A.2 (record mẫu B6 ở trang PDF 113) — tài liệu chứa 46 phát hiện trích dẫn.
- Đối chiếu: record [PSI-26 BASS](https://psi.nasa.gov/physci/repo/data/investigations/PSI-26) và PSI-25 BASS-II ở Mục 2.
- Bản PDF giới thiệu BASS của NASA Glenn: [BASS (PDF)](https://www.nasa.gov/wp-content/uploads/2025/07/burning-and-suppression-of-solids-bass.pdf)

### Pack 3 — LUCI (dữ liệu hiếm nhất, gần Mặt Trăng)
- **Nguồn chính:** Lunar Combustion Investigation, **NTRS 20250010653** — record thanh PMMA ở trang 20, toàn tài liệu có 6 phát hiện trích dẫn.

### Pack 4 — FLEX (biểu đồ số liệu lớn)
- **Đường nhanh:** tải thẳng bảng Experimental Table từ [PSI-69](https://psi.nasa.gov/physci/repo/data/investigations/PSI-69) — 275 dòng, không cần bóc PDF.
- **Đường gốc:** báo cáo NASA/TP-2015-216046 (Dietrich et al., 2015), Table IX trang PDF 26–34 — 276 test.
- ⚠️ Ba nguồn đếm lệch nhau: **284** (tóm tắt NTRS) / **276** (Table IX) / **275** (bảng PSI) — khi nộp phải ghi rõ dùng bản nào.
- Dự án mẫu đã trích Table IX → CSV thành công (tham khảo pipeline, không chép số của họ): [github.com/shaharia179/flame-in-freefall](https://github.com/shaharia179/flame-in-freefall)

### Pack 5 — Khoảng trống Moon / Mars
- Không có nguồn tải riêng: tự tổng hợp từ Pack 1–4. Kết quả tham chiếu: Moon Base = 0 bằng chứng trực tiếp / 9 tương tự / 4 cơ chế; Mars = 0 record.
- Bối cảnh căn cứ Mặt Trăng: [Flammability of Materials on the Moon — FM2](https://www.nasa.gov/glenn/glenn-expertise-space-exploration/physical-sciences-program/combustion-science/flammability-of-materials-on-the-moon-fm2/)

## 4. Tài liệu NASA Glenn theo investigation (PDF giới thiệu chính thức)

| Investigation | Link |
|---------------|------|
| ACME | [ACME (PDF)](https://www.nasa.gov/wp-content/uploads/2025/07/advanced-combustion-via-microgravity-experiments-acme.pdf) |
| SoFIE | [SoFIE](https://www.nasa.gov/glenn/glenn-expertise-space-exploration/physical-sciences-program/combustion-science/solid-fuel-ignition-and-extinction-sofie/) · [Trang mission SoFIE](https://science.nasa.gov/mission/sofie/) |
| FLARE | [FLARE](https://www.nasa.gov/glenn/glenn-expertise-space-exploration/physical-sciences-program/combustion-science/flammability-limits-at-reduced-g-experiment-flare/) |
| DAFT | [DAFT (PDF)](https://www.nasa.gov/wp-content/uploads/2025/07/dust-and-aerosol-measurement-feasibility-test-daft.pdf) |
| FLEX | [FLEX (PDF)](https://www.nasa.gov/wp-content/uploads/2025/07/flame-extinguishment-experiment-flex.pdf) |
| Saffire | [Saffire (PDF)](https://www.nasa.gov/wp-content/uploads/2025/07/spacecraft-fire-safety-demonstration-saffire.pdf) |
| SAME | [SAME (PDF)](https://www.nasa.gov/wp-content/uploads/2025/07/smoke-aerosol-measurement-experiment-same.pdf) |
| SLICE | [SLICE (PDF)](https://www.nasa.gov/wp-content/uploads/2025/07/structure-liftoff-in-combustion-experiment-slice.pdf) |
| SPICE | [SPICE (PDF)](https://www.nasa.gov/wp-content/uploads/2025/07/smoke-point-in-co-flow-experiment-spice.pdf) |
| CIR (phần cứng) | [Combustion Integrated Rack](https://www.nasa.gov/glenn/glenn-expertise-space-exploration/physical-sciences-program/science-facilities-on-the-iss/fluids-combustion-facility/) |
| MDCA (phần cứng) | [Multi-user Droplet Combustion Apparatus](https://www.nasa.gov/glenn/glenn-expertise-space-exploration/physical-sciences-program/combustion-science/multi-user-droplet-combustion-apparatus-mdca/) |

> Danh sách nguồn crawl đầy đủ mà backend đang dùng nằm sẵn trong repo: `backend/scripts/crawl_sources.txt`

## 5. Bài học sự cố và spinoff Trái Đất (chỉ là bài viết + ảnh, không có file dữ liệu)

| # | Link | Dùng để |
|---|------|---------|
| 1 | [Apollo 1](https://sma.nasa.gov/sma-disciplines/accllp/apollo-1) | Thẻ bài học: cháy cabin 100% O₂ |
| 2 | [Apollo 13](https://sma.nasa.gov/sma-disciplines/accllp/apollo-13) | Thẻ bài học: sự cố hệ thống O₂/điện |
| 3 | [Cháy trên trạm Mir, 1997](https://www.nasa.gov/history/25-years-ago-fire-aboard-space-station-mir/) | Thẻ bài học: cháy thật trên trạm, 10 ảnh |
| 4 | [Spinoff: Bình khí lính cứu hỏa](https://spinoff.nasa.gov/node/9582) | Nhánh ứng dụng Trái Đất |
| 5 | [Spinoff: Đầu báo cháy phân biệt khói/bụi](https://spinoff.nasa.gov/node/9407) | Nhánh ứng dụng Trái Đất — nối với trục Detection |
| 6 | [Spinoff: Công nghệ đồ bảo hộ chữa cháy](https://spinoff.nasa.gov/node/9918) | Nhánh ứng dụng Trái Đất |

## 6. Đối thủ tham khảo — MicroFire Atlas (học cách làm, không chép số)

- [Trang chủ](https://microfire-atlas.vercel.app/) · [Judge Tour](https://microfire-atlas.vercel.app/tour) · [Atlas 78 record](https://microfire-atlas.vercel.app/atlas) · [Compare](https://microfire-atlas.vercel.app/compare) · [Mission Lab](https://microfire-atlas.vercel.app/mission) · [Gaps](https://microfire-atlas.vercel.app/gaps) · [Sources — 19 tài liệu](https://microfire-atlas.vercel.app/sources) · [Methodology](https://microfire-atlas.vercel.app/methodology)
- Sự thật cần nhớ: 78 record của họ = 56 BASS/BASS-II + 20 Saffire + 2 LUCI; FLEX/SAME/ACME chỉ là trích dẫn cơ chế; cả site chỉ có 4 media; AI tổng hợp trực tiếp của họ còn ghi "coming soon".

## 7. File đã có sẵn trong repo này

| File | Nội dung |
|------|----------|
| `docs/wow-pack-flame-in-freefall.xlsx` | Checklist trích xuất Wow Pack: 26 đầu việc, 18 việc P0, schema GraphRAG 22 trường, kịch bản demo 90 giây |
| `docs/LINKS.md` | File này |
| `data/psi/` | Dữ liệu PSI thô đã tải về (11 GB, gitignore — không commit) |
| `data/profiles.json` | Metadata investigation đã crawl |
| `backend/scripts/crawl_sources.txt` | Danh sách nguồn backend đang crawl |

---

*Nguyên tắc trích dẫn: mọi con số trong sản phẩm phải lần được về 1 link trong file này kèm số trang/bảng. Không dùng ảnh AI làm bằng chứng thí nghiệm.*
