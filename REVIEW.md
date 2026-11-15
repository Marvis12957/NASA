---
phase: n/a (files-scoped review, không có GSD phase)
reviewed: 2026-11-15T13:17:00Z
depth: standard
files_reviewed: 24
files_reviewed_list:
  - backend/app/main.py
  - backend/app/orchestrator.py
  - backend/app/agents.py
  - backend/app/tools.py
  - backend/app/graph.py
  - backend/app/llm.py
  - backend/app/memory.py
  - backend/app/prompts.py
  - backend/app/knowledge.py
  - backend/app/rerank.py
  - backend/app/graphrag.py
  - backend/app/neo4j_config.py
  - backend/app/viz.py
  - backend/app/viz_planner.py
  - frontend/src/App.jsx
  - frontend/src/lib/api.js
  - frontend/src/lib/openui.jsx
  - frontend/src/components/ChatMessage.jsx
  - frontend/src/components/ComparePane.jsx
  - frontend/src/components/DataPane.jsx
  - frontend/src/components/LabPane.jsx
  - frontend/src/components/ReportPane.jsx
  - frontend/vite.config.js
  - backend/requirements.txt
findings:
  critical: 6
  warning: 18
  info: 11
  total: 35
status: issues_found
---

# Code Review Report (review theo danh sách file)

**Reviewed:** 2026-11-15T13:17:00Z (thời điểm snapshot)
**Depth:** standard
**Files Reviewed:** 24
**Status:** issues_found

## Summary

Phạm vi review: backend FastAPI + LangGraph (route → specialists → synthesize), lớp truy cập Neo4j Aura, guardrail LLM, hybrid retrieval/rerank, bộ composer FlameViz và frontend React/Vite. Không chạy gì vào mạng. Riêng phần định tuyến và nhận diện alias thì đã kiểm chứng offline bằng cách import `app.knowledge` / `app.orchestrator` / `app.prompts` (kết quả trích dẫn trong từng finding).

Các vấn đề chính:
1. **Sẵn sàng hoạt động (availability):** vòng retry HTTP 429 trong `llm._post` chạy vô hạn và bỏ qua timeout. Ngoài ra, khi Aura không kết nối được thì app không khởi động nổi, và mọi endpoint trả 500 sau khoảng 36 s.
2. **Tính năng hỏng end-to-end:** frontend không bao giờ gửi `sessionId`, nên session memory không hoạt động. Mỗi câu hỏi còn tạo thêm một node `ChatSession` trong Aura mà không có cơ chế dọn.
3. **Kết quả sai cho người dùng:** câu hỏi nêu tên một investigation kèm một từ chỉ môi trường (ví dụ "FLEX ... on the ISS") bị đổi sang chế độ xếp hạng và mất luôn investigation đã nêu. Mục "Nguồn ưu tiên" trong ReportPane thực chất chỉ là 5 bản ghi đầu tiên theo thứ tự chuỗi `psiId`.
4. **Bảo mật/chi phí:** API công khai qua Cloudflare tunnel, không có auth, không giới hạn độ dài câu hỏi, không rate-limit. Mỗi request gọi tới hàng chục lần LLM trả phí.

## Critical Issues

### CR-01: Retry HTTP 429 vô hạn, bỏ qua `timeout` và chặn threadpool của FastAPI

**File:** `backend/app/llm.py:224-240` (gọi từ `prompts.py:254`, `agents.py:208`, `viz_planner.py:191`)
**Issue:** Trong `_post`, nhánh 429 chạy `while True:` → `time.sleep(quota_wait_seconds(...))` → `continue`. Không có giới hạn số lần thử và không có deadline. `quota_wait_seconds` tối thiểu 15 s, mặc định 60 s, và nếu message có "Window resets at: ..." thì sleep tới lúc reset (có thể vài giờ). Tham số `timeout=45/60` mà `grounded_complete` truyền vào chỉ áp cho từng lần `urlopen`, không giới hạn tổng thời gian.
Kịch bản: provider hết quota → mỗi `/api/ask` (endpoint `def` đồng bộ chạy trong threadpool mặc định khoảng 40 worker) treo vô thời hạn. Chỉ cần khoảng 40 request là toàn bộ API, kể cả `/health` và `/api/investigations`, ngừng phản hồi. Trong `viz_planner._ask_llm`, `future.result(timeout)` trả về nhưng worker thread vẫn tiếp tục ngủ, nên thread bị rò rỉ dần.
**Fix:** Chỉ cho phép sleep-on-429 ở đường batch (extract/community), còn đường online thì fail nhanh:
```python
def _post(url, payload, headers, timeout, *, retry_429: bool = False, max_retries: int = 2):
    deadline = time.monotonic() + max(1, int(timeout))
    attempts = 0
    while True:
        remaining = int(deadline - time.monotonic())
        if remaining < 1:
            raise RuntimeError("LLM budget exhausted")
        try:
            return _urlopen_json(urllib.request.Request(url, data=body, headers=headers, method="POST"), remaining)
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and retry_429 and attempts < max_retries:
                attempts += 1
                time.sleep(min(quota_wait_seconds(...), max(0, deadline - time.monotonic())))
                continue
            raise RuntimeError(f"LLM HTTP {exc.code}") from None
```
Đường request (`complete_text` gọi từ prompts/agents/viz_planner) dùng `retry_429=False`.

### CR-02: Aura không kết nối được → app không khởi động; mọi endpoint 500 sau ~36 s; hợp đồng "HTTP 200 + MissingData" bị phá

**File:** `backend/app/neo4j_config.py:50-62`, `backend/app/graph.py:727-733`, `backend/app/main.py:41-55, 67-78, 168-175, 202-207`
**Issue:** `connect()` thử 6 lần, sleep 2+4+6+8+8+8 = 36 s rồi `raise last`. `open_store()` gọi `connect` trực tiếp. `lru_cache` không cache exception, nên mỗi request đều thử lại 36 s.
- `lifespan` gọi `open_store()` không có try. Khi Aura Free tự pause (sau vài ngày không dùng) hoặc mất mạng lúc khởi động, uvicorn báo "Application startup failed" và thoát.
- `get_store()` chỉ xử lý trường hợp `None` (chưa cấu hình). Lỗi kết nối là `ServiceUnavailable`/`AuthError`, không phải `HTTPException`, nên `_viz_or_missing` (docstring: "Neo4j faults stay HTTP 200 with MissingData") gọi `get_store()` nằm ngoài khối `try` thứ hai và trả 500.
- `/health` cũng gọi `open_store()`: nó treo 36 s rồi trả 500, thay vì trả `{"ok": false}`. Frontend vì vậy hiện "Ngoại tuyến" với lý do sai, và các probe giám sát cũng timeout.
**Fix:**
```python
@lru_cache(maxsize=1)
def open_store() -> GraphStore | None:
    uri, user, password, database = neo4j_settings()
    if not uri or not password:
        return None
    from neo4j import GraphDatabase
    driver = GraphDatabase.driver(uri, auth=(user, password))  # lazy; không verify ở đây
    return GraphStore(driver, database)
```
Giữ `connect()` có retry cho script CLI. Trong `health()` thì bọc `store.ping()` (đã có try). Trong `_viz_or_missing`/`extract.csv` thì đưa `get_store()` vào cùng khối `try` với builder, và chỉ re-raise `HTTPException(503)` khi store là `None`.

### CR-03: Frontend không gửi `sessionId` → memory follow-up không hoạt động, Aura tích lũy `ChatSession` vô hạn

**File:** `frontend/src/lib/api.js:14-23`, `backend/app/main.py:109-126`, `backend/app/memory.py:95-125`
**Issue:** `api.ask(question)` chỉ gửi `{ question }` (grep `sessionId` trong `frontend/src` không có kết quả). Backend thấy `sessionId` rỗng thì tạo `uuid4()` mới cho mỗi request. Hệ quả:
1. `apply_memory` không bao giờ có `turns`. Câu "còn nó thì sao?" hay "what about its fuels?" sau câu "Tell me about FLEX" sẽ không resolve được về PSI-69. Toàn bộ `memory.py` và `memory_notes` thành code chết trên đường UI.
2. `remember_cloud` vẫn chạy `MERGE (s:ChatSession {id: <uuid mới>})` + `CREATE (t:ChatTurn)` cho mỗi câu hỏi. Lệnh prune chỉ cắt trong phạm vi một session, nên số session tăng tuyến tính và không bao giờ bị xoá. Aura Free có giới hạn node/relationship (DB đang ở mức khoảng 20k node theo `api.js:40`), nên bot hoặc người dùng spam qua tunnel công khai có thể làm đầy DB tri thức.
3. `sessionId` do client tuỳ ý đặt, không validate độ dài hay định dạng (xem thêm WR-12).
**Fix:** Frontend giữ `sessionId` theo thread và gửi kèm, đồng thời dùng `sessionId` mà server trả về:
```js
async ask(question, sessionId) {
  const r = await fetch("/api/ask", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, sessionId }) });
  if (!r.ok) throw new Error(`/api/ask → HTTP ${r.status}`);
  return r.json(); // { answer, sessionId, ranked, chunks, ... }
}
```
Backend: thêm `sessionId: str | None = Field(None, max_length=64, pattern=r"^[A-Za-z0-9-]+$")`, gắn TTL cho `ChatSession` (property `lastSeen` kèm job dọn session cũ hơn N ngày), và chỉ ghi Aura khi client thực sự gửi `sessionId`.

### CR-04: API công khai không auth, không giới hạn input, không rate-limit, mà mỗi request kích hoạt nhiều cuộc gọi LLM trả phí

**File:** `backend/app/main.py:31-33, 58-64, 106-111`, `frontend/vite.config.js:13-29`
**Issue:** `vite.config.js` ghi rõ preview chạy sau Cloudflare tunnel công khai (`host: true`, `allowedHosts: true`) và proxy `/api` tới FastAPI mà không có lớp auth nào. `AskBody.question` chỉ có `min_length=0`, không có `max_length`. Một `/api/ask` có thể gọi LLM tới khoảng 10 lần, câu hỏi được nhúng nguyên văn vào mọi prompt: `choose_tool` ×2 assignment, brief ×2 kèm guardrail retry ×2, rồi coordinator kèm retry. Model mặc định là `claude-opus-5-5` (`llm.py:26`). Kịch bản: một script gửi câu hỏi 1 MB lặp lại → chi phí token tăng vọt, đồng thời regex `mentioned_ids`/`detect_*` chạy trên chuỗi khổng lồ. `CORSMiddleware(allow_origins=["*"])` còn cho phép bất kỳ trang web nào khiến trình duyệt nạn nhân gọi API nếu backend lộ ra ngoài (Tailscale/LAN). `allowedHosts: true` tắt cơ chế chống DNS-rebinding của Vite.
**Fix:**
```python
class AskBody(BaseModel):
    question: str = Field(..., min_length=1, max_length=1000)
    sessionId: str | None = Field(None, max_length=64, pattern=r"^[A-Za-z0-9-]+$")
```
Thêm rate-limit theo IP (ví dụ `slowapi`, hoặc Cloudflare rate-limiting rule), thêm shared token hoặc Cloudflare Access cho `/api/*`, đặt `allow_origins=[<origin tunnel>]`, và để `allowedHosts` bằng danh sách hostname cụ thể (`[".trycloudflare.com"]`).

### CR-05: ReportPane — "Nguồn ưu tiên" không theo kịch bản, chỉ là 5 bản ghi đầu theo thứ tự chuỗi `psiId`

**File:** `frontend/src/components/ReportPane.jsx:23`, các chỗ dùng: `:37, :46, :94-102`
**Issue:** `const priorities = investigations.slice(0, 5);`, trong đó `investigations` lấy từ `/api/investigations`, vốn chạy `ORDER BY i.psiId` (`graph.py:238`). Đây là sắp xếp chuỗi, nên kết quả luôn là PSI-10, PSI-100, PSI-101, PSI-102, PSI-106 cho mọi kịch bản (ISS, Moon, Suit...). Báo cáo tải về (`flame-brief.html`) trình bày danh sách này dưới tiêu đề "Nguồn ưu tiên" / "Đối chiếu NASA PSI", trong khi ứng dụng tự quảng bá là "không dựng số liệu". Đây là thông tin an toàn cháy nổ bị gán sai cho kịch bản. Danh sách cũng có thể mâu thuẫn với phần "Nhận định khoa học" (phần này dùng `RELEVANT_TO`).
**Fix:** Lấy danh sách từ ranking thật. Có thể cho `api.ask` trả về `ranked` (xem IN-04) rồi dùng `d.ranked.slice(0, 5)`, hoặc thêm endpoint `GET /api/rank/{scenario}` gọi `store.rank_investigations(scenario, None, 5)`:
```js
const { answer, ranked } = await api.ask(prompt);
setBrief({ scen, insight: answer, priorities: ranked.slice(0, 5) });
```

### CR-06: Định tuyến bỏ mất investigation đã nêu tên khi câu hỏi có từ chỉ môi trường hoặc khi so sánh với một id

**File:** `backend/app/orchestrator.py:41, 54-59, 66-83`
**Issue:** `wants_rank = bool(scenario) or ...`, nên bất kỳ từ nào khớp `SCENARIO_PATTERNS` ("ISS", "station", "cabin", "spacecraft", "habitat"...) đều bật chế độ rank. Nhánh `len(ids) == 1 and not wants_rank` vì thế bị bỏ qua và luồng rơi vào ranker + navigator, PSI đã nêu tên biến mất. Kiểm chứng offline:
- `"What did FLEX measure on the ISS?"` → ids `['PSI-69']` → route `['ranker', 'navigator']` với `{'scenario': 'iss'}`. Câu trả lời là top-5 xếp hạng ISS, không phải thẻ FLEX. Gần như mọi thí nghiệm PSI đều chạy trên ISS, nên đây là dạng câu hỏi tự nhiên nhất.
- `"compare FLEX with others"` → `['ranker', 'comparer']` với `{'scenario': 'iss', 'limit': 2}` và `{'fromRanker': True}`. `handle_comparer` chỉ dùng `ids_from_ranker`, nên FLEX không có trong bảng so sánh.
- `"Tell me about FLEX and SAFFIRE-II"` (2 id, không có từ so sánh) → chỉ chạy graphrag, không đọc thẻ nào.
**Fix:**
```python
if wants_compare and len(ids) == 1:
    return [step("ranker", ..., {"scenario": scenario or "iss", "role": role, "limit": 3}),
            step("comparer", "Compare with leaders", {"ids": ids, "fromRanker": True})]
explicit_rank = bool(re.search(r"\brank|relevant|priority|\btop\b|\bbest\b|which investigation|xếp hạng|...", q))
if ids and not explicit_rank:
    steps = [step("reader", "Read the investigation", {"id": i}) for i in ids[:2]]
    if wants_graph and len(ids) == 1:
        steps.append(step("navigator", "Walk the graph around it", {"id": ids[0]}))
    return steps
```
`handle_comparer` cần gộp `hints["ids"]` với `ids_from_ranker(prior)` khi có `fromRanker`, thay vì dùng `if/elif`.

## Warnings

### WR-01: `mentioned_ids` sinh PSI ảo từ alias chồng nhau và từ đơn vị "psi"

**File:** `backend/app/knowledge.py:195-206`
**Issue:** (a) Không loại bỏ match chồng span. Alias ngắn vẫn khớp bên trong alias dài khi ngăn cách bằng dấu cách. Kiểm chứng: `"Tell me about BASS II"` → `['PSI-25', 'PSI-26']`, `"FLEX 2 fuels"` → `['PSI-68', 'PSI-69']`, `"SPICE data analysis results"` → `['PSI-60', 'PSI-107']`. Vì có 2 id nên route bỏ qua reader và chỉ chạy graphrag (CR-06), người hỏi một thí nghiệm không nhận được thẻ của nó. (b) Regex `psi[-\s]?(\d+)` không có `\b` và nhầm đơn vị áp suất: `"suit at 4.3 psi 100% oxygen"` → `['PSI-100']` (SAFFIRE-III). Lỗi tương tự có ở `prompts._PSI_RE` (`prompts.py:78`), khiến guardrail đánh dấu "invented_psi:PSI-100" cho câu trả lời đúng có viết "4.3 psi 100% O2".
**Fix:** Bỏ qua match nằm trong span đã nhận và đòi hỏi tiền tố id:
```python
for match in re.finditer(r"(?<![\d.])\bPSI[-\s]?(\d+)\b(?!\s*%)", question, re.I): ...
taken: list[tuple[int, int]] = []
# với mỗi alias match (m.start(), m.end()): bỏ nếu nằm trong span đã có; duyệt alias dài trước.
```

### WR-02: Provider Gemini luôn ép `responseMimeType: application/json`, kể cả câu trả lời văn xuôi

**File:** `backend/app/llm.py:155-156, 303-316`
**Issue:** `complete_text` gọi `_gemini(info, system, user, timeout=...)` nhưng bỏ qua `json_mode`. `generationConfig` luôn đặt `"responseMimeType": "application/json"`. Khi chỉ cấu hình `GEMINI_API_KEY`/`GOOGLE_API_KEY`, mọi `grounded_complete` (brief, coordinator) trả về chuỗi JSON (ví dụ `{"answer": "..."}`) và chuỗi này được render thẳng vào chat. Ngoài ra, khi không có candidate, hàm trả `"{}"`, và với đường văn xuôi thì đây là câu trả lời "{}" hợp lệ, vượt qua guardrail.
**Fix:** Truyền `json_mode` vào và chỉ đặt `responseMimeType` khi `json_mode=True`. Khi không có candidate thì trả `""`.

### WR-03: Header xác thực Anthropic gửi khoá ở hai chỗ; `ENABLE_TOOL_SEARCH` bật web search cho câu trả lời "grounded"

**File:** `backend/app/llm.py:59-72, 281-291`
**Issue:** `_detect_anthropic` đặt `key = api_key or auth_token` và `auth_token = auth_token or api_key`. Kết quả là luôn gửi cả `x-api-key` và `Authorization: Bearer <cùng khoá>`. Khi chỉ có `ANTHROPIC_AUTH_TOKEN` (OAuth/gateway), token bị gửi vào `x-api-key`. Khi chỉ có `ANTHROPIC_API_KEY`, API key bị gửi như Bearer. Với `ANTHROPIC_BASE_URL` trỏ tới proxy bên thứ ba, cả hai header đều lộ ra ngoài, và một số gateway từ chối request có hai kiểu auth (401). Bên cạnh đó, `ENABLE_TOOL_SEARCH=1` gắn tool `web_search` cho mọi lời gọi, kể cả `grounded_complete`, trái với guardrail số 7 "Do not browse" (`prompts.py:30`). Nội dung web không nằm trong allowlist PSI/percent nên chỉ bị chặn một phần (con số không có "%" và tên thí nghiệm vẫn lọt qua).
**Fix:** Gửi đúng một header: `x-api-key` nếu có `ANTHROPIC_API_KEY`, ngược lại `Authorization: Bearer` với `ANTHROPIC_AUTH_TOKEN`. Thêm tham số `allow_tools=False` cho `complete_text` và không bao giờ bật web search trên đường grounded.

### WR-04: `/api/ask` không có ngân sách thời gian tổng; brief bị sinh lại; `global_search` chạy N+1 truy vấn Neo4j → vượt timeout tunnel

**File:** `backend/app/orchestrator.py:433-468, 482, 583-599`, `backend/app/agents.py:207-213`, `backend/app/prompts.py:253-272`, `backend/app/graph.py:615-629`
**Issue:** Với câu hỏi chung chung (route mặc định có 2 assignment graphrag), chuỗi lời gọi tuần tự là: `choose_tool` (30 s) + brief (45 s + retry 45 s), nhân 2, cộng coordinator (60 s + retry 60 s). Tổng có thể lên tới khoảng 360 s. `answer_from_agents` gọi `write_specialist_briefs(question, trace)` lần nữa, nên mọi step có brief thất bại ở `act_node` (LLM trả `None`) bị gọi LLM lại thêm một lượt. `global_search` gọi `self.get_investigation()` riêng cho từng `psiId` (tối đa 8) của mọi community có summary (tối đa 800 dòng) **trước** khi rerank lấy top-k. Mỗi lời gọi mở session mới tới Aura, nên một lượt global search có thể tạo hàng trăm round-trip. Cloudflare trả 524 sau khoảng 100 s, khi đó người dùng nhận thông báo lỗi kết nối dù backend vẫn đang chạy (và vẫn tốn token).
**Fix:** Đặt một deadline cho mỗi request (ví dụ 75 s) trong state LangGraph và truyền `timeout=min(x, remaining)` cho từng lời gọi LLM. Bỏ lượt brief thứ hai trong `answer_from_agents` (dùng `state["prior_briefs"]`). Trong `global_search`, rerank trước rồi mới hydrate investigation cho k community còn lại, bằng một truy vấn `UNWIND $ids ... MATCH (i:Investigation {psiId: id})`.

### WR-05: Exception từ Neo4j trong tool không được bắt → `/api/ask` trả 500 thay vì câu trả lời suy giảm

**File:** `backend/app/agents.py:15-30`, `backend/app/tools.py:244-248`, `backend/app/main.py:110-111`
**Issue:** `execute()` gọi `fn(args, store)` trực tiếp. Các hàm như `store.rank_investigations`, `get_investigation`, `compare_investigations`, `scenario_context` dùng `QUERY_TIMEOUT_SECONDS = 10` và có thể ném `ClientError`/`TransientError`/`ServiceUnavailable`. Không tầng nào bắt (kể cả `_step`, `run_assignment`, LangGraph, `main.ask`). Kịch bản: Aura chậm hơn 10 s ở câu rank → toàn bộ request 500, mất cả phần GraphRAG đã chạy xong. `initial_state` → `investigation_names()` cũng không được bảo vệ.
**Fix:**
```python
def execute(name, args, store):
    fn = IMPLEMENTATIONS.get(name)
    if not fn:
        return fail(f"Unknown tool {name}")
    try:
        return fn(args, store)
    except Exception as exc:  # noqa: BLE001
        return fail(f"{name} failed: {type(exc).__name__}")
```

### WR-06: Guardrail phần trăm chỉ ghi nhận cận trên của khoảng "a–b%" → con số bịa vẫn lọt, câu đúng lại bị chặn

**File:** `backend/app/prompts.py:79, 92-96, 112-116, 160-162`; nguồn chuỗi: `backend/app/knowledge.py:142`
**Issue:** `format_oxygen` tạo `"21–30% oxygen"`. `_PCT_RE = r"\b\d+(?:\.\d+)?\s*%"` chỉ bắt `"30%"` (kiểm chứng: `_pct_norm("21–30% oxygen") == {'30%'}`). Hệ quả: (a) LLM viết "15–30% oxygen" (cận dưới bịa) vẫn qua guardrail, vì `"15"` không có `%` nên không bị kiểm tra; (b) LLM viết đúng nghĩa "từ 21% đến 30%" thì `"21%"` bị coi là "invented_percent" → retry → `None` → rơi về template.
**Fix:** Chuẩn hoá khoảng thành cả hai đầu khi xây allowlist và khi kiểm tra:
```python
_RANGE_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*[–-]\s*(\d+(?:\.\d+)?)\s*%")
def _pct_norm(text):
    found = {f"{a}%" for pair in _RANGE_RE.findall(text or "") for a in pair}
    found |= {re.sub(r"\s+", "", m) for m in _PCT_RE.findall(text or "")}
    return found
```

### WR-07: `_validate_choice` cho phép LLM ghi đè `role`/`limit` của router và đổi loại tool của navigator

**File:** `backend/app/agents.py:137-147, 161-173`
**Issue:** Docstring ghi "Keep the router's scenario and PSI ids", nhưng `role = args.get("role") if ... in {...} else hints.get("role")` cho phép LLM thêm bộ lọc role mà người dùng không hỏi. Ranker vì vậy trả về tập con, và `_result_empty` không phát hiện vì vẫn còn vài dòng. `limit` cũng bị LLM nâng từ 2 (nhánh "Pick the leading investigations" cho so sánh) lên 8, khiến comparer so sánh 4 thay vì 2. Với navigator có `hints["id"]` (route "reader + navigator"), LLM có thể chọn `scenario_context` với scenario bất kỳ hợp lệ. Bước này không bao giờ bị coi là rỗng, nên phần "Walk the graph around it" bị thay bằng ghi chú kịch bản.
**Fix:** Khi `hints` có `role`/`limit` thì giữ nguyên giá trị đó (`role = hints.get("role")`, `limit = hints.get("limit", ...)`). Với navigator: nếu `hints` có `id` thì chỉ chấp nhận `graph_neighbors`; nếu có `scenario` thì chỉ chấp nhận `scenario_context`.

### WR-08: `detect_scenario` chọn sai môi trường do thứ tự và pattern lỏng

**File:** `backend/app/knowledge.py:77-84`
**Issue:** Thứ tự là mars → rover → suit → earth → moon → iss, và pattern moon có `habitat`. Vì vậy "fire in the ISS habitat module" → `moon`. Pattern suit `eva\b` thiếu `\b` đầu nên "Geneva" → `suit`. Pattern iss có `cabin`, nên "Apollo 1 cabin fire" (dữ kiện lịch sử thuộc kịch bản suit/pure-O2) → `iss`. Do `wants_rank = bool(scenario)` (CR-06), các nhận diện sai này kéo theo xếp hạng sai môi trường.
**Fix:** Ưu tiên token định danh mạnh (`\biss\b`, `\bmoon\b|lunar`, `\bmars\b`) trước token yếu (`habitat`, `cabin`, `station`), và sửa `\beva\b`. Hoặc tính điểm theo số match rồi chọn môi trường có điểm cao nhất.

### WR-09: `/api/viz` — `state.focusId` ghi đè investigation nêu tên trong câu hỏi

**File:** `backend/app/viz.py:1743-1749, 1768, 1780-1783`
**Issue:** `_focus` trả `state["focusId"]` trước `named[0]`. Kịch bản: client đang xem hồ sơ FLEX (`focusId="PSI-69"`) rồi hỏi "Hồ sơ SAFFIRE-II" → intent `profile` nhưng render lại PSI-69. Tương tự với "đồ thị BASS-II".
**Fix:** `return named[0] if named else (state focusId)`, tức là tên nêu rõ trong câu hỏi luôn thắng ngữ cảnh.

### WR-10: Dataset CSV xuất ra không có cột định danh investigation

**File:** `backend/app/viz.py:35-46, 1339-1349, 1358`, `backend/app/main.py:213`
**Issue:** `EXTRACT_COLUMNS` không chứa `"id"`, dù `_extract_rows` có tính `row["id"]` và sort theo nó. `extract.csv` và `DataTable` của view extract chỉ có `acronym, family, ...`. Với investigation có `acronym` rỗng (`_blank` trả `""`), dòng dữ liệu không thể nối lại về PSI nào. Đây là lỗi mất dữ liệu cho mục đích "train model" mà view coverage quảng bá.
**Fix:** `EXTRACT_COLUMNS = ("id", "acronym", ...)` và cập nhật header fallback ở `main.py:213`. Nên trung hoà thêm công thức CSV (giá trị bắt đầu bằng `= + - @`) khi ghi.

### WR-11: BM25 tokenizer loại bỏ tiếng Việt có dấu và token ≤ 2 ký tự (O2, CO2)

**File:** `backend/app/rerank.py:15-23`
**Issue:** `_TOKEN = r"[a-z0-9]+"` cùng điều kiện `len(tok) > 2` khiến "nhiên liệu" chỉ còn token `nhi`, "phát hiện khói" chỉ còn rác như `ph`/`hi`, và "O2", "CO2" bị loại hẳn. UI hoàn toàn bằng tiếng Việt (gợi ý mặc định là câu tiếng Việt), MiniLM là mô hình tiếng Anh, nên nhánh BM25 của RRF gần như vô dụng hoặc nhiễu với câu hỏi tiếng Việt. `lexical_scores` (fallback rerank) cũng vậy.
**Fix:** Dùng `re.compile(r"\w+", re.UNICODE)` kèm chuẩn hoá `unicodedata.normalize("NFKD")` để bỏ dấu, giữ các token 2 ký tự có chữ số (`o2`, `co2`). Cân nhắc embedding đa ngôn ngữ (ví dụ `paraphrase-multilingual-MiniLM-L12-v2`) cho query.

### WR-12: Session memory — `sessionId` do client đặt, đọc chéo session, `SESSIONS` cục bộ không giới hạn

**File:** `backend/app/memory.py:31-57, 95-125, 176-183`, `backend/app/main.py:109-110`
**Issue:** Ai biết hoặc đoán được `sessionId` (không có validate, có thể là "test", "1"...) thì đọc được các câu hỏi trước của session đó. `apply_memory` đưa nguyên văn `earlier question “...”` vào `memory_notes`, rồi notes được truyền làm "Specialist briefs" cho coordinator, nên câu trả lời LLM có thể lộ câu hỏi của người khác và có thể bị prompt-injection qua câu hỏi cũ. `SessionMemory._sessions` (fallback khi Aura lỗi) không giới hạn số session, nên bộ nhớ tăng vô hạn. `ChatTurn.at = timestamp()` theo mili-giây, hai turn cùng ms thì `ORDER BY t.at` không xác định. Memory notes còn được chèn cả khi câu hỏi không phải follow-up, làm tăng khả năng guardrail loại bỏ câu trả lời (PSI trong notes không thuộc allowlist).
**Fix:** Server tự cấp `sessionId` ngẫu nhiên (đã có `uuid4`) và chỉ chấp nhận id do server cấp (ký HMAC hoặc kiểm tra tồn tại). Dùng LRU/TTL cho `SESSIONS`. Chỉ thêm `memory_notes` khi `carried["follow"]` là True, và chỉ chứa id/scenario, không chứa câu hỏi nguyên văn.

### WR-13: Chat — bấm "Trò chuyện mới"/mở thread khác trong lúc đang chờ trả lời sẽ ghi đè cuộc trò chuyện mới

**File:** `frontend/src/App.jsx:149-159, 161-182, 184-185`
**Issue:** `send` giữ closure `base` (tin nhắn cũ) và `persistThread` (với `threadId` cũ). Nếu người dùng bấm `newChat()` hoặc `openThread(t)` trong khi `/api/ask` đang chạy (thường mất hàng chục giây, xem WR-04), khi response về thì `setMessages(done)` dán lại toàn bộ hội thoại cũ lên màn hình mới, và `persistThread(done)` ghi vào thread cũ hoặc tạo thread mới sai. `setThreadId` lại được gọi bên trong updater của `setThreads`; StrictMode gọi updater hai lần nên có thể sinh hai id `t${Date.now()}`.
**Fix:** Gắn token cho mỗi lượt gửi (`const turn = ++turnRef.current`) và bỏ qua kết quả nếu `turnRef.current !== turn` hoặc thread đã đổi. Tạo `threadId` trước khi gọi `setThreads`, không tạo trong updater.

### WR-14: Bảng `NAME_TO_ID` của frontend gán sai investigation cho thẻ "làm giàu" hiển thị cạnh câu trả lời

**File:** `frontend/src/App.jsx:30-41, 74-92`
**Issue:**
- `/\bsame\b/i` → PSI-102: câu "Is the flame the same in microgravity?" hiện thẻ SAME (PSI-102) cùng bảng "Chi tiết hồ sơ".
- `/flex/i` không có biên từ → "flexible materials", "reflex" → PSI-69. "FLEX-2" cũng → PSI-69 (đúng ra là PSI-68).
- `/bass/i` → PSI-25, trong khi backend map `bass` → PSI-26 và `bass-ii` → PSI-25 (`knowledge.py:92-93`). Câu trả lời backend và thẻ frontend nói về hai thí nghiệm khác nhau.
- `/acme/i` → PSI-10. ACME là cơ sở thí nghiệm chung, không phải một investigation.
**Fix:** Không tự nhận diện ở frontend. Để `/api/ask` trả `focusId`/`ranked` (đã có trong response) và dựng thẻ từ đó. Nếu vẫn giữ thì dùng biên từ và đồng bộ với `ALIASES` của backend.

### WR-15: ComparePane hiển thị skeleton mãi khi một lần tải chi tiết thất bại

**File:** `frontend/src/components/ComparePane.jsx:25-32, 60-61`
**Issue:** `api.investigation(id)...catch(() => {})` nuốt lỗi. `details[id]` không bao giờ được đặt, nên `chosen.some((id) => !details[id])` luôn đúng và skeleton quay vô hạn, không có thông báo lỗi hay nút thử lại. Effect chỉ chạy lại khi `sel`/`ids.length` đổi, nên id lỗi không bao giờ được thử lại.
**Fix:** Lưu trạng thái lỗi: `.catch((e) => setDetails((m) => ({ ...m, [id]: { error: e.message } })))`, rồi render cột lỗi thay cho skeleton.

### WR-16: Số liệu "kiểm kê trực tiếp từ Neo4j Aura" thực chất là hằng số viết cứng

**File:** `frontend/src/lib/api.js:40-50`, `frontend/src/App.jsx:51-54`
**Issue:** `DB_STATS` và `CHUNK_SOURCES` được hard-code, nhưng UI chú thích "Số liệu kiểm kê trực tiếp từ Neo4j Aura, 15/11/2026". Mỗi lần re-ingest hoặc mỗi `ChatSession` mới tạo (CR-03) làm số hiển thị sai lệch thêm, trong một ứng dụng tự nhận là "không dựng số liệu". Backend đã có `/api/viz/coverage` cung cấp số đếm thật.
**Fix:** Gọi `/api/viz/coverage` (hoặc một endpoint `/api/stats` đơn giản) và hiển thị `MissingData` khi lỗi. Nếu vẫn giữ hằng số thì ghi rõ "ảnh chụp ngày ..., không cập nhật".

### WR-17: `/health` công khai lộ URI Aura và username

**File:** `backend/app/main.py:67-78`
**Issue:** Endpoint không cần auth trả `"uri": "neo4j+s://<id>.databases.neo4j.io"` và `"username"`. Qua tunnel công khai (CR-04), kẻ tấn công biết ngay endpoint DB và tài khoản để dò mật khẩu. Frontend chỉ dùng `ok`/`neo4j`.
**Fix:** Chỉ trả `{"ok": ..., "neo4j": ...}`. Nếu cần thông tin chẩn đoán thì đặt sau cờ debug hoặc auth.

### WR-18: `requirements.txt` không có cận trên, không lock, lẫn dependency dev và script

**File:** `backend/requirements.txt:1-8`
**Issue:** `langgraph>=0.2`, `sentence-transformers>=3.0`, `fastapi>=0.115` đều không có cận trên. Một lần `pip install` mới có thể kéo phiên bản major tiếp theo (đổi API `StateGraph`/`CrossEncoder`, kéo `torch` mới) và làm hỏng build demo. `pytest` nằm trong dependency runtime. `networkx` chỉ dùng trong `backend/scripts/community.py`. Không có lockfile hay hash, nên build không tái lập được (frontend thì có `package-lock.json`).
**Fix:** Pin theo dạng `langgraph>=0.2,<0.3` (hoặc phiên bản đang chạy), tách `requirements-dev.txt` (pytest) và `requirements-scripts.txt` (networkx), sinh lock bằng `pip-compile --generate-hashes`.

## Info

### IN-01: `viz_planner.py` không được nối vào endpoint nào (module chết)

**File:** `backend/app/viz_planner.py:490-523, 590-597`; `backend/app/main.py:141-145`
**Issue:** `/api/viz` gọi `viz.compose`, không phải `compose_planned`, và cũng không gọi `remember`. Toàn bộ planner (khoảng 600 dòng) chỉ được test dùng. Ngoài ra, `_run_step` bắt `TypeError` từ `compose_explore` rồi gọi lại không kèm scenario (`:279-282`), che mất bug thật bên trong composer. Frontend cũng không gọi `/api/viz*` nào.
**Fix:** Nối vào endpoint (theo `docs/AGENT_GENUI.md`) hoặc gỡ bỏ. Bỏ `try/except TypeError`.

### IN-02: Code thừa và nhánh chết

**File:** `backend/app/orchestrator.py:102-130, 641-652` (và các field `plan`, `wants_graph`, `limit`, `compare_ids` trong `AgentState`); `backend/app/main.py:170-173, 204-207`; `backend/app/graphrag.py:213`
**Issue:** `plan_nodes` chỉ dùng để ghi vào `messages`. `ask()`/`agent_catalog()` trùng lặp với `main.py`. `try: get_store() except HTTPException: raise` không có tác dụng. `gazetteer_prefix == "inv"` ở `graphrag.py:213` không bao giờ đúng vì đã `return` ở `:208-209`, nên `created` luôn là `False`.
**Fix:** Xoá phần thừa, hoặc dùng `plan` thật trong dispatch.

### IN-03: Chuỗi placeholder tiếng Anh bị cắt sai và mâu thuẫn chú thích "—"

**File:** `frontend/src/App.jsx:77-83`, `frontend/src/components/ComparePane.jsx:12-14, 93`, `frontend/src/components/DataPane.jsx:23-25`
**Issue:** Backend luôn trả `oxygen = "no oxygen column in the tables held here"` (truthy), nên nhánh "Chưa trích xuất" không bao giờ chạy. `o2.replace(" oxygen", "")` còn biến chuỗi này thành "no column in the tables held here". `flow` cũng vậy ("no airflow column..."). ComparePane chú thích ô "—" là thiếu dữ liệu nhưng lại hiện câu tiếng Anh dài.
**Fix:** Kiểm tra `oxygenMin == null` thay vì kiểm tra truthy của `oxygen`. Dùng nhãn tiếng Việt thống nhất.

### IN-04: `api.ask` bỏ mất `ranked`, `chunks`, `trace`, `focusId` → UI không hiện trích dẫn

**File:** `frontend/src/lib/api.js:21-22`
**Issue:** Chỉ trả về `d.answer`. Toàn bộ bằng chứng (chunk id, trang, community) mà backend dày công dựng bị bỏ, nên người dùng không kiểm chứng được câu trả lời "grounded".
**Fix:** Trả cả object và render `EvidencePanel`/danh sách nguồn.

### IN-05: `URL.revokeObjectURL` gọi ngay sau `a.click()`

**File:** `frontend/src/components/ChatMessage.jsx:52-58`, `frontend/src/components/ReportPane.jsx:44-51`, `frontend/src/components/LabPane.jsx:67-74`
**Issue:** Trên một số trình duyệt (Safari, Firefox cũ), revoke đồng bộ có thể huỷ tải xuống.
**Fix:** `setTimeout(() => URL.revokeObjectURL(href), 0)` hoặc sau khoảng 1 s.

### IN-06: CsvTool tách CSV bằng `split(",")`

**File:** `frontend/src/components/DataPane.jsx:76-78`
**Issue:** Trường có dấu phẩy trong ngoặc kép, hoặc header có ngoặc kép, làm lệch cột. Các dòng lệch bị lặng lẽ loại (`filter(r.length === head.length)`), nên số dòng báo cáo sai mà không cảnh báo.
**Fix:** Dùng parser CSV (papaparse) hoặc ít nhất báo số dòng bị bỏ.

### IN-07: Chuẩn hoá PSI id không thống nhất

**File:** `backend/app/knowledge.py:172-174, 195-196` và `backend/app/graphrag.py:111-115`
**Issue:** `resolve_psi_id("PSI-069")` trả `"PSI-069"` (không khớp node `PSI-69`), trong khi `graphrag.normalize_psi_token` dùng `int()`, nên cùng một chuỗi cho kết quả khác nhau.
**Fix:** Dùng chung một hàm chuẩn hoá `f"PSI-{int(d)}"`.

### IN-08: Khởi tạo lười không thread-safe

**File:** `backend/app/graph.py:152-158, 146-150, 117-128, 727-733`
**Issue:** Các endpoint đồng bộ chạy song song. Request đồng thời đầu tiên có thể nạp `SentenceTransformer` và BM25 hai lần (tăng RAM đột biến), và `open_store` có thể tạo hai driver (rò rỉ một).
**Fix:** Dùng `threading.Lock` quanh khởi tạo lười, hoặc warm-up trong `lifespan`.

### IN-09: `CATALOG_READ_LIMIT = 40` cắt cụt danh mục mà không cảnh báo

**File:** `backend/app/graph.py:47, 117-128, 232-242`
**Issue:** Hiện catalog có 26 PSI. Khi vượt 40, `investigation_names`/`list_investigations` lặng lẽ bỏ các bản ghi cuối (theo thứ tự chuỗi), khiến alias theo tên và danh sách UI thiếu mà không ai biết.
**Fix:** Nâng limit, hoặc log/cảnh báo khi `len(rows) == limit`.

### IN-10: `_extinction_blocks` đưa nguyên văn `c.text` vào EvidencePanel

**File:** `backend/app/viz.py:1594-1600, 1636`
**Issue:** Các view khác dùng `preview_text` (420 ký tự), còn view này trả nguyên chunk dài, gây payload lớn và không nhất quán.
**Fix:** Áp `preview_text(row["preview"])` trước khi đưa vào `_evidence_block`.

### IN-11: `LabPane` không kiểm tra kiểu dữ liệu localStorage

**File:** `frontend/src/components/LabPane.jsx:52-54`
**Issue:** Nếu `flame-runs` là JSON hợp lệ nhưng không phải mảng (ví dụ `{}`), `runs.map` sẽ crash cả pane. Ở `App.jsx:116-118`, `threads` có cùng vấn đề.
**Fix:** `const v = JSON.parse(...); return Array.isArray(v) ? v : [];`

---

_Reviewed: 2026-11-15T13:17:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
