# AGENT_GENUI — Agent lắp giao diện theo ngữ cảnh câu hỏi

| | |
|---|---|
| **Ngày** | 15/11/2026 |
| **Phạm vi** | Chỉ agent (backend) và dữ liệu. Không sửa frontend, không sửa `app/viz.py`, `app/main.py`, `app/graph.py` |
| **Trạng thái** | Đã có code và test, **chưa nối vào endpoint** (xem mục 7) |
| **Liên quan** | `docs/AGENT_VIZ_SPEC.md` (catalog component, UISpec) |

## 1. Vấn đề

`POST /api/viz` hiện chọn màn hình bằng regex (`viz.classify`). Mỗi câu hỏi ra đúng một kiểu view, nên:

- Câu ghép nhiều ý chỉ được một nửa. "So sánh FLEX và BASS-II, kèm cảm biến mỗi bên dùng" ra ma trận cảm biến, mất bảng so sánh.
- Câu hỏi nối tiếp không hiểu "nó". "Còn thiết bị của nó thì sao?" rơi về hỏi đáp.
- Diễn đạt lệch mẫu thì đi sai view. "So với FLEX thì khác gì?" ra hồ sơ FLEX thay vì bảng so sánh.
- Câu lạc đề vẫn trả về 5 chunk không liên quan.

## 2. Cách giải

Một lần gọi LLM đọc câu hỏi cùng ngữ cảnh phiên rồi chọn 1 đến 3 view trong danh sách đóng. LLM chỉ trả tên view, mã PSI, mã kịch bản và câu truy vấn. Mọi con số vẫn do các hàm `compose_*` của `app/viz.py` lấy từ Aura, nên planner không có đường nào để bịa số.

```
câu hỏi + state (focusId, filters, sessionId) + các lượt trước
        │
        ▼
 build_context ──► LLM (1 lần gọi, có giới hạn thời gian) ──► kế hoạch JSON
        │                                                        │
        │                                              validate_plan (loại view lạ, id lạ)
        │                                                        │
        │                                   chạy từng view bằng viz.compose_*
        │                                                        │
        └── lỗi ở bất kỳ bước nào ──► viz.compose (luật cũ)   merge_specs ──► UISpec
```

Kế hoạch LLM trả về:

```json
{"offTopic": false,
 "views": [{"view": "compare", "ids": ["PSI-69", "PSI-25"]},
           {"view": "sensor",  "ids": ["PSI-69", "PSI-25"]}]}
```

11 view được phép: `explore`, `profile`, `compare`, `graph`, `scenario`, `sensor`, `topic`, `coverage`, `extract`, `evidence`, `qa`.

## 3. File

| File | Vai trò |
|---|---|
| `backend/app/viz_planner.py` | Planner. Hàm chính `compose_planned(store, question, state=None, turns=None)` |
| `backend/tests/test_viz_planner.py` | 15 test, LLM và composer đều giả lập, không cần mạng |
| `backend/scripts/build_field_evidence.py` | Dựng chỉ mục nguồn của khoảng oxy và số lần chạy (mục 6) |
| `backend/var/viz_field_evidence.json` | Kết quả của script trên |

Cả bốn đều là file mới. Không file nào có sẵn bị sửa.

## 4. Luật kiểm tra kế hoạch (`validate_plan`)

- View không nằm trong 11 view thì bỏ.
- Mã PSI phải có trong `store.investigation_names()`, không thì bỏ.
- `profile` và `graph` cần một mã. Thiếu thì lấy focus của phiên, không có focus thì bỏ view.
- `compare` cần 2 đến 3 mã. Chỉ có một mã thì ghép với focus, vẫn thiếu thì bỏ view.
- `scenario` phải thuộc `iss, moon, mars, rover, suit, earth`.
- Tối đa 3 view, view trùng thì gộp.
- Không còn view nào hợp lệ thì dùng luật cũ.

Khi ghép (`merge_specs`):

- Block có component ngoài catalog 17 component bị loại.
- Nhiều `EvidencePanel` gộp thành một, tối đa 5 chunk, đặt cuối trang.
- `followups` lấy xen kẽ từ các view, tối đa 3.
- `intent` là intent của view đầu tiên. Khung UISpec giữ đúng mục 7 của spec.
- Thêm một trường `planner` để truy vết: `{"mode": "llm" | "rules" | "snapshot", "ms", "views", "reason"}`.
- View `sensor` có kèm mã PSI thì ma trận chỉ giữ các cột đó. Số trong ô được chép nguyên, không tính lại.

## 5. Ngữ cảnh mà planner dùng

| Nguồn | Dùng để |
|---|---|
| Câu hỏi | Chọn view |
| Mã PSI nhắc trong câu (`mentioned_from_question`) | Gợi ý cho LLM |
| `state.focusId` hoặc filter `investigation` | Hiểu "nó", "cái đó" |
| `state.filters` | Truyền cho `explore`, báo cho LLM bộ lọc đang bật |
| `state.sessionId` | Đọc 4 lượt gần nhất từ `app.memory` (đã lưu ở Aura) |
| Danh sách 25 investigation (mã + tên) | Để LLM đổi tên thành mã, không tự nghĩ ra mã |

Bộ nhớ phiên chỉ lưu câu hỏi, mã focus, kịch bản, các mã đã xếp hạng và tên view. Không lưu câu trả lời hay con số, giống quy ước của `app/memory.py`.

Câu lạc đề: LLM trả `offTopic: true`, planner trả UISpec gồm một block `MissingData` (field `scope`), một dòng thông báo và 3 chip gợi ý. Không gọi tìm chunk.

## 6. Dữ liệu: nguồn của khoảng oxy

Spec yêu cầu GaugeO2 phải kèm nguồn, và nghiệm thu F2/F10 đòi mở bằng chứng thấy chunk ghi 21,5–22,1% của PSI-99. Điều này không thể đạt bằng chunk: khoảng oxy được `app/catalog.py` tính từ file CSV Experiment Table, mà CSV không được cắt thành chunk. PSI-99 chỉ có 5 chunk và không chunk nào chứa hai con số đó.

`build_field_evidence.py` ghi lại đúng nguồn gốc theo cùng luật với `catalog.py`:

```
~/venvs/nasa-api/bin/python backend/scripts/build_field_evidence.py --check
```

- Đọc `data/psi/*/Experiment Table/*.csv` và `data/profiles.json`. Không dùng Neo4j, không dùng LLM.
- Mỗi investigation: `oxygenMin`, `oxygenMax`, `runCount`, và với từng cột oxy: tên cột, min, max, số giá trị, dòng chứa min và max (số thứ tự dòng dữ liệu, ô gốc, mã mẫu nếu bảng có).
- `--check` đối chiếu với `backend/var/catalog.json`. Kết quả ngày 15/11: 25 investigation, 0 lệch.
- 9 investigation có khoảng oxy: PSI-10, 20, 25, 39, 68, 69, 98, 99, 159. 5 investigation không có bảng: PSI-60, 62, 101, 115, 117.

Ví dụ PSI-99: cột `Percent O2 (Note 1)`, 11 giá trị trên 15 dòng, min ở dòng 1 (mẫu 2-1, ô `~ 21.5`), max ở dòng 2 (mẫu 2-2, ô `~ 22.1`).

Planner dùng chỉ mục này trong `attach_field_evidence`: GaugeO2 nào chưa có evidence chỉ tới file thì được thêm tham chiếu tới file CSV và cột. Chỉ thêm khi min/max trong bảng trùng với min/max của gauge, và giữ nguyên evidence đang có.

## 7. Cách bật

Chưa nối vì `app/main.py` và `app/viz.py` đang được sửa liên tục trong ngày 15/11. Khi sẵn sàng, đổi endpoint trong `backend/app/main.py`:

```python
from app.viz_planner import compose_planned, remember

@app.post("/api/viz")
def viz(body: VizBody) -> dict:
    store = get_store()
    spec = compose_planned(store, body.question, body.state)
    remember(store, body.state, body.question, spec)   # chỉ ghi khi state có sessionId
    return spec
```

`compose_planned` không ném exception. `remember` ghi một `ChatTurn` vào Aura cho mỗi câu hỏi có `sessionId`.

Biến môi trường:

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `VIZ_PLANNER` | `1` | `0` thì luôn dùng luật cũ, không gọi LLM |
| `VIZ_PLANNER_TIMEOUT` | `12` | Số giây tối đa cho cả bước gọi LLM, gồm cả các lần thử lại |

Thứ tự ưu tiên khi chạy: câu rỗng → luật cũ; có snapshot → snapshot; planner tắt → luật cũ; còn lại → LLM, lỗi thì luật cũ.

## 8. Kết quả thử (15/11/2026, đồ thị thật, LLM thật)

Chạy trong tiến trình, không qua HTTP, 12 câu. Cả 12 đi đường LLM, 12/12 đúng khung UISpec và chỉ dùng component trong catalog. Thời gian 1,0 đến 7,9 giây, trung vị 3,1 giây (luật cũ: 0,1 đến 1,1 giây).

| Câu hỏi | Luật cũ | Planner |
|---|---|---|
| So sánh FLEX và BASS-II, kèm cảm biến mỗi bên dùng | sensor | compare + sensor (lọc 2 cột) |
| Cho xem hồ sơ Saffire-II và đồ thị quan hệ của nó | graph | profile + graph |
| Còn thiết bị của nó thì sao? (lượt trước xem PSI-99) | qa | graph PSI-99 |
| So với FLEX thì khác gì? (lượt trước xem PSI-99) | profile FLEX | compare PSI-99 và PSI-69 |
| Tell me about the oxygen source used by Saffire | evidence | evidence |
| Nếu là trên Lunar Gateway thì nên đọc thí nghiệm nào? | scenario | scenario moon |
| What is the capital of France? | qa, 5 chunk không liên quan | ngoài phạm vi, không chunk |
| Dữ liệu đã đủ để train model chưa? Xuất bảng luôn | extract | coverage + extract |
| Cảm biến nào phát hiện khói tốt trong SAME? | sensor | sensor, lọc PSI-102 |
| Which experiments studied PMMA flame spread? | qa | explore (chưa lọc theo PMMA) |
| Tổng quan các chủ đề an toàn cháy | topic | topic |
| asdf qwer zxcv | qa, 5 chunk | ngoài phạm vi |

Test tự động: `backend/tests/test_viz_planner.py` 15/15. Cả bộ `backend/tests` (trừ `test_pdf_captions.py`, thiếu `pymupdf` trong venv `nasa-api`): 104/104.

## 9. Giới hạn đã biết

- **Proxy LLM trả rỗng.** Khoảng một phần ba đến một nửa số lần gọi tới proxy trả về text rỗng sau 0,2 giây (`output_tokens: 0`). Planner thử lại tối đa 3 lần trong giới hạn thời gian. Lượt chạy đầu chưa có thử lại: 6/12 câu rơi về luật cũ. Sau khi thêm: 12/12 đi đường LLM.
- **Kế hoạch không cố định.** Cùng một câu, hai lần gọi có thể ra hai kế hoạch khác nhau dù `temperature` là 0. Kết quả luôn hợp lệ nhờ `validate_plan`, nhưng demo nên dùng snapshot cho các câu chuẩn.
- **Chậm hơn luật cũ** vài giây mỗi câu và tốn quota LLM.
- **`explore` chưa lọc theo nhiên liệu từ câu hỏi.** Câu về PMMA vẫn ra đủ 25 thẻ. Cần cho LLM trả thêm filter `fuel`.
- **Chưa có đường HTTP.** Mọi số đo ở mục 8 là gọi hàm trực tiếp.
- **Frontend chưa đọc trường `planner`** và chưa gửi `sessionId`, nên hỏi nối tiếp chỉ hoạt động khi client gửi `state.sessionId` hoặc `state.focusId`.

## 10. Việc tiếp theo (agent và dữ liệu)

1. Nối endpoint theo mục 7 khi `main.py` ổn định.
2. Tạo `backend/var/viz_snapshots.json` cho khoảng 10 câu demo, sinh bằng `compose_planned`.
3. Cho kế hoạch mang filter (`fuel`, `family`) để `explore` và `graph` thu hẹp theo câu hỏi.
4. Dùng `viz_field_evidence.json` cho StatCard "Số lần chạy" (đã có `runCount` và số dòng từng bảng).
5. Cắt các file CSV Experiment Table thành chunk dạng bảng nếu muốn nghiệm thu F10 đạt bằng chunk thật.
