# Agent fixes — handoff for integration into `orchestrator.py`

> **Status 16:45 UTC:** final three-way merge installed (answer language + tables + rank-lock / quote answers + the hooks below + input prompt-injection guardrail). pytest 197 passed. Copy: `backend/var/claude-merge/orchestrator.final.py`. Agents: re-read files from disk and make small edits; whole-file writes from a cached copy erased this work twice.

> **Status 16:24 UTC:** the hooks below were re-attached by Claude on top of the answer-language version; `test_intent.py` passes. This note stays as reference. Separate issue: the 16:13 rewrite also dropped ~400 lines of rank-lock / quote-answer / comparison routing (see review-son.md); 9 tests in `test_agents.py` fail because of that.

Written 2026-11-15 by Claude (review session for Son). Context: `review-son.md` at the repo root.

Between 15:52 and 16:15 UTC a set of agent-quality fixes was applied. The rewrite of
`backend/app/orchestrator.py` for per-user answer language (`answer_language`, backup
`orchestrator.py.bak-lang-2315`) replaced the file and dropped the orchestrator hooks below.
Everything else is still in place. This note lists what to re-wire so it fits the new
language design. **Do not restart the API before this is done**: the API started at
16:07 UTC still runs the hooked version, and a restart loses those behaviours.

## Still in place (no action needed)

| File | Change |
|---|---|
| `backend/app/intent.py` | New module: `analyze()` (English search query and off-topic flag, one cached LLM call), `refine_route()`, `retrieval_status()`, `relevant_hits()`, `retrieval_from_trace()`, `retrieval_note()`, `is_meta_question()`, `previous_questions()`, `history_answer()`, `off_topic_text()`. The language helpers in it were since extended by the other agent. |
| `backend/app/llm.py` | `complete_text()` retries an empty reply up to 2 times inside the timeout (`EMPTY_REPLY_RETRIES`). Measured: 11–18% of calls came back empty. |
| `backend/app/knowledge.py` | `mentioned_ids()`: "4.3 psi 100%" is no longer PSI-100; "BASS II" / "FLEX 2" no longer also add PSI-26 / PSI-69. `detect_scenario()`: "flame on Earth vs microgravity" is not the Earth scenario; "ISS habitat" is ISS; `\beva\b`. `resolve_psi_id()` accepts "PSI 25" and drops leading zeros. |
| `backend/app/tools.py` | `local_search` stores `data["retrieval"]` (strong / weak / none, from the cross-encoder score) and drops passages scored ≤ −3 (unrelated). |
| `backend/app/prompts.py` | Guardrail percents: both ends of "12.0–34.0%", 30 == 30.0, decimal comma, fuel-mixture percents allowed. Rule 5 asks to say *which* "don't know" it is. No "relevance None". No system internals or "limitations" section; catalog rows are cited by PSI id. |
| `backend/app/evidence_filter.py` | `[ocr: false]` is **not** a garbage signal (it is on 99.5% of PDF chunks and means "had a text layer"). Before this fix nearly every PDF passage was dropped. |
| `backend/app/graph.py` | `preview_text()` strips the `[ocr: …]` marker, so quotes no longer show a stray `false]`. |
| `backend/scripts/eval_agent.py` + `eval_questions.json` | Live evaluation, 16 questions (VI + EN). |
| `backend/tests/test_intent.py` | 12 of its tests fail until the hooks below are back (route repair, off-topic, history). |

## Hooks to re-add in `orchestrator.py`

Use `from app import intent as question_intent` (module import, so tests can monkeypatch).

1. **`AgentState`**: add `search_query: str`, `off_topic: bool`, `meta: bool`, `previous_questions: list[str]`.

2. **`initial_state()`**, after `named = carried["named_ids"]`:
   ```python
   analysis = question_intent.analyze(trimmed)          # {"lang", "query", "offTopic"}
   meta = question_intent.is_meta_question(trimmed)
   ...
   "search_query": analysis["query"],                     # English, for retrieval only
   "off_topic": bool(analysis["offTopic"]) and not named and not meta,
   "meta": meta,
   "previous_questions": question_intent.previous_questions(memory),
   ```
   The off-topic and history replies should use the new `answer_language`, not `analysis["lang"]`.
   `off_topic_text(lang)` and `history_answer(previous, lang)` take "vi" / "en" today; map
   `answer_language` to them, or let them take the language name.

3. **`route_node`**, before `named = ...`:
   ```python
   if state.get("meta"):
       return {"plan": [], "assignments": [], "mode": "history",
               "answer": question_intent.history_answer(state.get("previous_questions") or [], <lang>)}
   if state.get("off_topic"):
       return {"plan": [], "assignments": [], "mode": "offtopic",
               "answer": question_intent.off_topic_text(<lang>)}
   ```
   and replace `assignments = route(question, named, scenario=remembered)` with
   ```python
   assignments = question_intent.refine_route(
       question, named, route(question, named, scenario=remembered),
       state.get("search_query") or question,
   )
   ```
   `refine_route` reads a named investigation even when the question names an environment,
   sends concept comparisons ("ngọn lửa trên Trái Đất và trong vi trọng lực") to document
   search, adds a document search to reader **and to ranking answers** (so a ranking answer has
   passages to cite; today it has only catalog rows), and points every search at the English
   query (MiniLM and BM25 only work on English: a Vietnamese query scored −9.9 vs +4.5 for its
   English version).

4. **`synthesize_node`**, first lines:
   ```python
   if state.get("mode") in {"offtopic", "history"}:
       return {"answer": state.get("answer") or "", "mode": state.get("mode")}
   ```

5. **`answer_from_agents(...)` call in `synthesize_node`**:
   ```python
   memory_notes=(state.get("memory_notes") or [])
   + question_intent.retrieval_note(question_intent.retrieval_from_trace(trace)),
   ```

6. **`format_ask()` return dict**: `"retrieval": question_intent.retrieval_from_trace(state.get("trace") or [])`
   (`main.py` already forwards `answer.get("retrieval")` in `/api/ask` and the stream `meta` event).

The original patch scripts are in `/tmp/agentfix/` on the server (`apply_agent_fixes.py`,
`apply_history_fix.py`, `apply_citation_fix.py`), and the pre-change files are in
`/tmp/agent-fix-backup-20261115T154158/`. Both are in `/tmp` and disappear when the container restarts.

## Verify after integrating

```bash
.venv/bin/python -m pytest backend/tests          # test_intent.py must pass again
.venv/bin/python backend/scripts/eval_agent.py     # ~100 LLM calls, about 7 minutes
```

The last evaluation with the hooks in place: 13/16. The three failures had causes that were fixed afterwards:
- `[ocr: false]` filter;
- "SAME" read as the English word "same": the query now spells out acronyms;
- `_compose_graphrag_answer` (added the same afternoon) answers document questions with raw English quotes instead of an LLM summary. It is anti-hallucination by design, but it ignores the answer language. Worth deciding whether it should stay.
