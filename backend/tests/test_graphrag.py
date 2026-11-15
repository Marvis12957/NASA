"""GraphRAG helpers and community presence. Skip live LLM/Aura when missing."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.graphrag import (  # noqa: E402
    is_drawing_leftover,
    is_forbidden_entity,
    resolve_entity,
    should_skip_chunk,
)
from app.llm import detect_provider, quota_wait_seconds, _post  # noqa: E402
from app.graph import GraphStore  # noqa: E402
from app.orchestrator import compose, format_ask, plan_nodes, route  # noqa: E402
from app.tools import SPEC_MAP  # noqa: E402


def test_quota_wait_seconds_uses_reset_window() -> None:
    from datetime import datetime, timezone

    now = datetime(2026, 11, 14, 10, 27, tzinfo=timezone.utc)
    msg = (
        'Token quota exceeded. Window resets at: 2026-11-14T17:27:09.039884. '
        "Weekly quota remaining."
    )
    wait = quota_wait_seconds(msg, now=now)
    assert wait >= 7 * 3600
    assert wait < 8 * 3600
    assert quota_wait_seconds("HTTP 429", retry_after="90", now=now) == 90.0
    assert quota_wait_seconds("HTTP 429", now=now) == 60.0


def test_forbidden_oxygen_entities() -> None:
    assert is_forbidden_entity("21% O2", "Material/Fuel")
    assert is_forbidden_entity("0.21 mole fraction of O2")
    assert is_forbidden_entity("oxygen")
    assert is_forbidden_entity("XO2")
    assert not is_forbidden_entity("n-heptane", "Material/Fuel")
    assert not is_forbidden_entity("cool flame", "Phenomenon")


def test_skip_short_and_drawing_chunks() -> None:
    assert should_skip_chunk({"id": "a", "token_estimate": 40, "text": "cool flame on ISS"})
    assert is_drawing_leftover(
        {
            "id": "b",
            "token_estimate": 200,
            "source": "data/psi/ACME_common/Engineering Documents/ACME_drawings_igniter.zip/s1101mfa0400.pdf",
            "text": "REV B SHEET 1 OF 1",
        }
    )
    assert not should_skip_chunk(
        {
            "id": "c",
            "token_estimate": 120,
            "source": "data/psi/PSI-69_FLEX/Reports/flex.pdf",
            "text": "Cool flame extinction of n-heptane droplets was observed aboard the ISS.",
        }
    )


def test_scoped_extract_ids_do_not_collapse_labels() -> None:
    mapping = {
        "pmma": "fuel:pmma",
        "methanol": "fuel:methanol",
        "flex": "inv:PSI-69",
        "cir": "hw:combustion-integrated-rack-cir-acme",
    }
    known = set(mapping.values()) | {"inv:PSI-25", "inv:PSI-69", "inv:PSI-99", "inv:PSI-39", "inv:PSI-159"}
    pmma_25 = resolve_entity("PMMA", "Material/Fuel", mapping, known, psi="PSI-25")
    pmma_99 = resolve_entity("PMMA", "Material/Fuel", mapping, known, psi="PSI-99")
    assert pmma_25["id"] == "fuel:pmma@PSI-25"
    assert pmma_99["id"] == "fuel:pmma@PSI-99"
    assert pmma_25["canonical"] == "fuel:pmma"
    sensor = resolve_entity("CIR", "Sensor", mapping, known, psi="PSI-69")
    hardware = resolve_entity("CIR", "Hardware", mapping, known, psi="PSI-69")
    assert sensor["id"] == "sensor:cir@PSI-69"
    assert hardware["id"] == "hardware:cir@PSI-69"
    cool_39 = resolve_entity("cool flame", "Phenomenon", mapping, known, psi="PSI-39")
    cool_159 = resolve_entity("cool flame", "Phenomenon", mapping, known, psi="PSI-159")
    assert cool_39["id"] == "phenomenon:cool-flame@PSI-39"
    assert cool_159["id"] == "phenomenon:cool-flame@PSI-159"
    inv = resolve_entity("FLEX", "Investigation", mapping, known, psi="PSI-25")
    assert inv["id"] == "inv:PSI-69"
    methanol = resolve_entity("methanol", "Material/Fuel", mapping, known, psi="PSI-69")
    assert methanol["id"] == "fuel:methanol@PSI-69"
    assert methanol["canonical"] == "fuel:methanol"
    assert "oxygen" not in pmma_25
    assert pmma_25["extracted"] is True


def test_hierarchical_communities_have_parent_links() -> None:
    sys.path.insert(0, str(BACKEND / "scripts"))
    from community import communities_from_edges  # noqa: E402

    rows = []
    for i in range(12):
        rows.append(
            {
                "src": f"fuel:pmma@PSI-{i}",
                "srcLabel": "Fuel",
                "srcName": "PMMA",
                "srcPsi": f"PSI-{i}",
                "dst": "phenomenon:flame-spread@PSI-25" if i < 6 else "phenomenon:soot@PSI-10",
                "dstLabel": "Phenomenon",
                "dstName": "flame spread" if i < 6 else "soot",
                "dstPsi": "PSI-25" if i < 6 else "PSI-10",
                "rel": "RELATED_TO",
            }
        )
        rows.append(
            {
                "src": f"inv:PSI-{i}",
                "srcLabel": "Investigation",
                "srcName": f"PSI-{i}",
                "srcPsi": f"PSI-{i}",
                "dst": f"fuel:pmma@PSI-{i}",
                "dstLabel": "Fuel",
                "dstName": "PMMA",
                "dstPsi": f"PSI-{i}",
                "rel": "TESTED_FUEL",
            }
        )
    communities = communities_from_edges(rows)
    levels = {int(item["level"]) for item in communities}
    assert 0 in levels and 1 in levels
    by_id = {item["id"]: item for item in communities}
    fine = [item for item in communities if item["level"] == 0]
    assert fine
    assert all(item.get("parent_id") in by_id for item in fine)
    for extra in range(12, 80):
        rows.append(
            {
                "src": f"inv:PSI-{extra}",
                "srcLabel": "Investigation",
                "srcName": f"PSI-{extra}",
                "srcPsi": f"PSI-{extra}",
                "dst": "hardware:cir@PSI-69" if extra % 2 == 0 else "hardware:msg@PSI-25",
                "dstLabel": "Hardware",
                "dstName": "CIR" if extra % 2 == 0 else "MSG",
                "dstPsi": "PSI-69" if extra % 2 == 0 else "PSI-25",
                "rel": "USED_HARDWARE",
            }
        )
    large = communities_from_edges(rows)
    large_levels = {int(item["level"]) for item in large}
    assert 0 in large_levels and 1 in large_levels
    if 2 in large_levels:
        by_large = {item["id"]: item for item in large}
        for item in large:
            if int(item["level"]) < 2:
                assert item.get("parent_id") in by_large


def test_tools_include_local_and_global() -> None:
    assert "local_search" in SPEC_MAP
    assert "global_search" in SPEC_MAP


def test_route_default_is_graphrag_local_then_global() -> None:
    steps = route("soot particle size in microgravity smoke", [])
    assert [step["agentId"] for step in steps] == ["graphrag", "graphrag"]
    assert steps[0]["hints"].get("mode") != "global"
    assert steps[1]["hints"].get("mode") == "global"
    assert plan_nodes("soot particle size in microgravity smoke", []) == [
        "extract_or_search",
        "global_community",
    ]


def test_route_global_community() -> None:
    question = "What fire-safety themes appear across PSI communities?"
    steps = route(question, [])
    assert steps[0]["agentId"] == "graphrag"
    assert steps[0]["hints"]["mode"] == "global"
    assert plan_nodes(question, []) == ["global_community"]


def test_plan_compare_named() -> None:
    assert plan_nodes("Compare FLEX vs BASS-II", ["PSI-69", "PSI-25"]) == ["compare"]


def test_catalog_rank_separate_from_oxygen() -> None:
    import json

    catalog_path = BACKEND / "var" / "catalog.json"
    if not catalog_path.is_file():
        pytest.skip("catalog.json not built")
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    rows = catalog["investigations"]
    iss_top = max(rows, key=lambda item: item["scores"]["iss"])
    moon_top = max(rows, key=lambda item: item["scores"]["moon"])
    suit_top = max(rows, key=lambda item: item["scores"]["suit"])
    assert iss_top["id"] == "PSI-102"
    assert moon_top["id"] == "PSI-159"
    assert suit_top["id"] == "PSI-159"
    assert iss_top["scores"]["iss"] != iss_top.get("oxygenMax")


def test_compose_oxygen_from_investigation_not_community_text() -> None:
    composed = compose(
        [
            {
                "tool": "global_search",
                "result": {
                    "ok": True,
                    "data": {
                        "degraded": False,
                        "communities": [
                            {
                                "id": "c1:cool-flame",
                                "title": "Cool flame",
                                "summary": "Ignore the 21% oxygen mentioned in this paragraph.",
                                "psiIds": ["PSI-69"],
                                "investigations": [
                                    {
                                        "id": "PSI-69",
                                        "name": "FLEX",
                                        "oxygen": "15–40% oxygen",
                                    }
                                ],
                            }
                        ],
                    },
                },
            }
        ]
    )
    assert "Catalog oxygen (Investigation properties): PSI-69 15–40% oxygen" in composed["text"]
    assert "Ignore the 21% oxygen" in composed["text"]


def test_compose_quotes_include_scoped_entity_ids() -> None:
    composed = compose(
        [
            {
                "tool": "local_search",
                "result": {
                    "ok": True,
                    "data": {
                        "hits": [
                            {
                                "chunkId": "ch-1",
                                "psi": "PSI-25",
                                "page": 3,
                                "preview": "PMMA flame spread in the MSG.",
                                "investigation": {
                                    "id": "PSI-25",
                                    "name": "BASS-II",
                                    "fuels": ["PMMA"],
                                    "oxygen": "13–25% oxygen",
                                },
                                "neighbors": [
                                    {
                                        "id": "fuel:pmma@PSI-25",
                                        "label": "PMMA",
                                        "type": "Fuel",
                                    }
                                ],
                            }
                        ]
                    },
                },
            }
        ]
    )
    assert "fuel:pmma@PSI-25" in composed["text"]
    assert "13–25% oxygen" in composed["text"]
    assert "PMMA flame spread" in composed["text"]


def test_global_search_degrades_when_summaries_empty() -> None:
    store = GraphStore.__new__(GraphStore)
    store.driver = None
    store.database = "neo4j"
    store._model = None
    store._chunk_text = {}
    store._names = {}
    rows = [
        {
            "id": "c0:soot",
            "summary": "",
            "title": "soot, smoke",
            "psiIds": ["PSI-102"],
            "level": 0,
            "size": 4,
            "parentId": "c1:aerosol",
        },
        {
            "id": "c1:aerosol",
            "summary": "",
            "title": "aerosol pillar",
            "psiIds": ["PSI-102", "PSI-101"],
            "level": 1,
            "size": 8,
            "parentId": None,
        },
    ]
    store.run = lambda *_args, **_kwargs: rows  # type: ignore[method-assign]
    store.get_investigation = lambda psi_id: {  # type: ignore[method-assign]
        "id": psi_id,
        "name": "SAME" if psi_id == "PSI-102" else psi_id,
        "oxygen": "21% oxygen",
        "fuels": ["Teflon"],
    }
    result = store.global_search("smoke aerosol themes across PSI", limit=3)
    assert result["degraded"] is True
    assert result["reason"] == "summaries_pending"
    assert result["communities"]
    assert result.get("rerank") in {"lexical", "cross-encoder"}
    by_id = {row["id"]: row for row in result["communities"]}
    assert "c1:aerosol" in by_id
    assert by_id["c1:aerosol"]["investigations"][0]["oxygen"] == "21% oxygen"


def test_format_ask_exposes_chunks_and_communities() -> None:
    packed = format_ask(
        {
            "question": "soot",
            "answer": "ok",
            "trace": [],
            "ranked": [],
            "chunks": [{"chunkId": "ch-1"}],
            "communities": [{"id": "c0:1"}],
            "focus_id": None,
            "mode": "search",
        }
    )
    assert packed["chunks"][0]["chunkId"] == "ch-1"
    assert packed["communities"][0]["id"] == "c0:1"


def test_urlerror_timeout_becomes_runtime_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import socket
    import urllib.error
    from app import llm

    def boom(*_args, **_kwargs):
        raise urllib.error.URLError(socket.timeout("timed out"))

    monkeypatch.setattr(llm.urllib.request, "urlopen", boom)
    with pytest.raises(RuntimeError, match="timed out"):
        _post("http://example.invalid/v1", {}, {"Content-Type": "application/json"}, timeout=2)


def test_wall_clock_timeout_around_urlopen(monkeypatch: pytest.MonkeyPatch) -> None:
    import time
    from app import llm

    class _Hang:
        def __enter__(self):
            time.sleep(1.5)
            return self

        def __exit__(self, *_args):
            return False

        def read(self) -> bytes:
            return b"{}"

    monkeypatch.setattr(llm.urllib.request, "urlopen", lambda *_args, **_kwargs: _Hang())
    with pytest.raises(RuntimeError, match="timed out"):
        _post("http://example.invalid/v1", {}, {"Content-Type": "application/json"}, timeout=1)


def test_community_summary_checkpoint_resumes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sys.path.insert(0, str(BACKEND / "scripts"))
    import community as community_mod

    calls = {"n": 0}

    def fake_summarize(item: dict) -> str:
        calls["n"] += 1
        return f"LLM summary for {item['id']}"

    monkeypatch.setattr(community_mod, "summarize", fake_summarize)
    communities = [
        {
            "id": "community:L0:0",
            "level": 0,
            "title": "PMMA",
            "parent_id": "community:L1:0",
            "size": 2,
            "psiIds": ["PSI-25"],
            "members": ["fuel:pmma@PSI-25", "inv:PSI-25"],
            "memberNames": ["PMMA", "PSI-25"],
            "summary": "",
        },
        {
            "id": "community:L1:0",
            "level": 1,
            "title": "pillar",
            "parent_id": None,
            "size": 2,
            "psiIds": ["PSI-25"],
            "members": ["fuel:pmma@PSI-25", "inv:PSI-25"],
            "memberNames": ["PMMA", "PSI-25"],
            "summary": "",
        },
    ]
    checkpoint = tmp_path / "graphrag_community.jsonl"
    first = community_mod.fill_summaries(
        communities,
        checkpoint=checkpoint,
        skip_summaries=False,
        api="anthropic/test",
        session=None,
    )
    assert first["summaries_written"] == 2
    assert calls["n"] == 2
    assert checkpoint.is_file()
    again = [
        {**item, "summary": ""}
        for item in communities
    ]
    second = community_mod.fill_summaries(
        again,
        checkpoint=checkpoint,
        skip_summaries=False,
        api="anthropic/test",
        session=None,
    )
    assert second["resumed"] == 2
    assert second["summaries_written"] == 0
    assert calls["n"] == 2
    assert again[0]["summary"].startswith("LLM summary")


def test_local_search_keeps_community_payload() -> None:
    store = GraphStore.__new__(GraphStore)
    store.driver = None
    store.database = "neo4j"
    store._model = object()
    store._chunk_text = {"ch-1": "PMMA flame spread"}
    store._bm25 = None
    store._names = {}
    store.embed_query = lambda _q: [0.0, 0.1]  # type: ignore[method-assign]
    store.run = lambda *_args, **_kwargs: [  # type: ignore[method-assign]
        {
            "chunkId": "ch-1",
            "psi": "PSI-25",
            "page": 3,
            "source": "flex.md",
            "text": "PMMA flame spread",
            "score": 0.9,
            "inv": None,
            "family": None,
            "neighbors": [{"id": "fuel:pmma@PSI-25", "label": "PMMA", "type": "Fuel", "rel": "MENTIONS"}],
            "communities": [
                {
                    "id": "community:L0:0",
                    "title": "PMMA spread",
                    "level": 0,
                    "parentId": "community:L1:0",
                }
            ],
        }
    ]
    result = store.local_search("PMMA flame spread", limit=3)
    assert result["retrieve"] == "hybrid"
    assert result["degraded"] is False
    assert result["hits"][0]["communities"][0]["id"] == "community:L0:0"
    assert result["hits"][0]["neighbors"][0]["id"] == "fuel:pmma@PSI-25"


def test_communities_exist_or_skip_without_llm() -> None:
    from app.graph import open_store
    from app.neo4j_config import neo4j_settings

    uri, _user, password, _database = neo4j_settings()
    if not uri or not password:
        pytest.skip("Aura not configured")
    try:
        store = open_store()
        if not store or not store.ping():
            pytest.skip("Aura unreachable")
        rows = store.run("MATCH (c:Community) RETURN count(c) AS n")
    except Exception:  # noqa: BLE001
        pytest.skip("Aura unreachable")
    count = int(rows[0]["n"]) if rows else 0
    if count == 0:
        pytest.skip("Community nodes not written yet; global search degrades until --with-communities finishes")
    assert count >= 500
    levels = store.run(
        "MATCH (c:Community) RETURN c.level AS level, count(*) AS n ORDER BY level"
    )
    assert {int(row["level"]) for row in levels} >= {0, 1, 2}
    search = store.global_search("cool flame droplet ISS", limit=3)
    assert search["degraded"] is False
    assert search["communities"]
