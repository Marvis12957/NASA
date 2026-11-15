"""Live evaluation of the agent crew on a fixed question set (calls the real LLM and Aura).

Usage (from the repo root, with .env):
    .venv/bin/python backend/scripts/eval_agent.py [--only N] [--out backend/var/eval_agent_report.json]

Each question in eval_questions.json may check:
  expect_any / expect_all  PSI ids that must appear in the ranked rows, focus, cited chunks or answer
  expect_top               PSI id that must be ranked first
  not_named                PSI ids the parser must NOT pick up from the question
  off_topic                the crew must answer with the off-topic message and run no tool
  lang                     'vi' answers must be written in Vietnamese
  retrieval                minimum retrieval strength ('strong' or 'weak')

It tells apart "the library has it but the crew did not find it" (retrieval none/weak while
an expected PSI exists) from a wrong answer. Costs roughly 4-8 LLM calls per question.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.graph import open_store  # noqa: E402
from app.intent import language_of  # noqa: E402
from app.orchestrator import compiled_graph, format_ask, initial_state  # noqa: E402

QUESTIONS = Path(__file__).with_name("eval_questions.json")
STRENGTH = {"none": 0, "unknown": 1, "weak": 2, "strong": 3}


def mentioned(answer: dict) -> set[str]:
    found = {str(row.get("id")) for row in answer.get("ranked") or [] if row.get("id")}
    if answer.get("focusId"):
        found.add(str(answer["focusId"]))
    for hit in answer.get("chunks") or []:
        if hit.get("psi"):
            found.add(str(hit["psi"]))
    found |= {f"PSI-{int(n)}" for n in re.findall(r"PSI-(\d+)", answer.get("text") or "")}
    return found


def check(case: dict, state0: dict, answer: dict) -> list[str]:
    problems = []
    seen = mentioned(answer)
    if case.get("off_topic"):
        if answer.get("mode") != "offtopic" or answer.get("trace"):
            problems.append(f"expected off-topic, got mode {answer.get('mode')}")
        return problems
    if answer.get("mode") == "offtopic":
        return ["wrongly treated as off-topic"]
    if case.get("expect_top"):
        ranked = [row.get("id") for row in answer.get("ranked") or []]
        if not ranked or ranked[0] != case["expect_top"]:
            problems.append(f"top {ranked[:1]} != {case['expect_top']}")
    if case.get("expect_all") and not set(case["expect_all"]) <= seen:
        problems.append(f"missing {sorted(set(case['expect_all']) - seen)}")
    if case.get("expect_any") and not (set(case["expect_any"]) & seen):
        problems.append(f"none of {case['expect_any']} found")
    for psi in case.get("not_named") or []:
        if psi in (state0.get("named_ids") or []):
            problems.append(f"parser picked up {psi}")
    if case.get("lang") == "vi" and language_of(answer.get("text") or "") != "vi":
        problems.append("answer not in Vietnamese")
    want = case.get("retrieval")
    got = (answer.get("retrieval") or {}).get("status", "none")
    if want and STRENGTH.get(got, 0) < STRENGTH[want]:
        problems.append(f"retrieval {got} < {want}")
    if re.search(r"guardrail|evidence block|ALLOWED EVIDENCE", answer.get("text") or "", re.I):
        problems.append("answer leaks system internals")
    return problems


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", type=int, default=0, help="run only the first N questions")
    parser.add_argument("--out", default=str(ROOT / "backend/var/eval_agent_report.json"))
    args = parser.parse_args()
    cases = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    if args.only:
        cases = cases[: args.only]
    store = open_store()
    if store is None:
        sys.exit("Neo4j Aura is not reachable; check .env")
    graph = compiled_graph(store)
    rows = []
    for case in cases:
        started = time.time()
        state0 = initial_state(store, case["q"])
        try:
            answer = format_ask(graph.invoke(state0))
            problems = check(case, state0, answer)
        except Exception as exc:  # noqa: BLE001
            answer, problems = {}, [f"error {type(exc).__name__}: {exc}"]
        row = {
            "question": case["q"],
            "pass": not problems,
            "problems": problems,
            "seconds": round(time.time() - started, 1),
            "mode": answer.get("mode"),
            "retrieval": answer.get("retrieval"),
            "searchQuery": state0.get("search_query"),
            "ranked": [r.get("id") for r in answer.get("ranked") or []][:5],
            "answer": (answer.get("text") or "")[:600],
        }
        rows.append(row)
        mark = "PASS" if row["pass"] else "FAIL"
        print(f"{mark} {row['seconds']:>5}s {row['mode'] or '-':9} {case['q'][:60]}  {'; '.join(problems)}", flush=True)
    passed = sum(1 for row in rows if row["pass"])
    print(f"\n{passed}/{len(rows)} passed")
    Path(args.out).write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"report: {args.out}")


if __name__ == "__main__":
    main()
