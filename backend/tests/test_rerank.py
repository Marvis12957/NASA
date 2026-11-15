from app.prompts import QUERY_SYSTEM, format_query_user
from app.rerank import BM25Index, lexical_scores, reciprocal_rank_fusion, rerank_records, rerank_scores


def test_bm25_prefers_rare_exact_term() -> None:
    index = BM25Index(
        [
            ("a", "flame spread over PMMA slabs in CIR"),
            ("b", "hardware drawings bolt torque table"),
            ("c", "cool flame soot aerosol SAME droplet"),
        ]
    )
    hits = index.search("cool flame soot", limit=2)
    assert hits[0][0] == "c"
    assert hits[0][1] > hits[1][1]


def test_rrf_boosts_ids_in_both_lists() -> None:
    fused = reciprocal_rank_fusion(
        [
            ["vec-only", "both", "vec-2"],
            ["bm25-only", "both", "bm25-2"],
        ]
    )
    assert fused[0][0] == "both"


def test_lexical_rerank_prefers_overlapping_doc() -> None:
    query = "cool flame soot ISS"
    docs = [
        "CIR hardware drawings and bolt torque table",
        "Cool flame soot and droplet extinction on the ISS",
        "unrelated rover wheel dust",
    ]
    scores = lexical_scores(query, docs)
    assert scores[1] > scores[0]
    assert scores[1] > scores[2]


def test_rerank_records_orders_and_tags_method() -> None:
    records = [
        {"id": "a", "preview": "bolt pattern CIR"},
        {"id": "b", "preview": "cool flame soot on ISS droplets"},
    ]
    ranked, method = rerank_records("cool flame soot", records, text_key="preview", limit=1)
    assert method in {"lexical", "cross-encoder"}
    assert ranked[0]["id"] == "b"
    assert "rerankScore" in ranked[0]


def test_rerank_scores_respect_lexical_env(monkeypatch) -> None:
    monkeypatch.setenv("RERANK_CROSS_ENCODER", "0")
    scores, method = rerank_scores("soot", ["soot aerosol SAME", "unrelated"])
    assert method == "lexical"
    assert scores[0] > scores[1]


def test_query_system_prompt_forbids_invented_oxygen() -> None:
    assert "HARD GUARDRAILS" in QUERY_SYSTEM
    user = format_query_user(
        "lunar habitat oxygen",
        chunks=[{"chunkId": "PSI-159/x", "psi": "PSI-159", "preview": "CFI-G burner", "investigation": {"oxygen": "30–85%"}}],
        communities=[{"id": "community:L2:1", "level": 2, "summary": "gaseous flames in CIR"}],
    )
    assert "PSI-159/x" in user
    assert "community:L2:1" in user
    assert "ALLOWED EVIDENCE" in user


def test_guardrail_flags_invented_psi_and_percent() -> None:
    from app.prompts import evidence_allowlist, guardrail_violations

    allow = evidence_allowlist(
        ranked=[{"id": "PSI-159", "oxygen": "30–85% oxygen", "fuels": ["propane"]}],
        chunks=[],
        communities=[],
    )
    clean = "CFI-G (PSI-159) catalog oxygen is 30–85% oxygen."
    assert guardrail_violations(clean, allow) == []
    assert guardrail_violations("BASS (PSI-25) burns at 21%.", allow)
    assert any("invented_psi" in reason for reason in guardrail_violations("See PSI-99 cool flames.", allow))
