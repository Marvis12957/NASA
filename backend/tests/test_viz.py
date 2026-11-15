"""UISpec composer. No Aura required."""

import csv
import io
import json

from app.viz import classify, compose, compose_graph, compose_profile, extract_csv, missing


class Store:
    def __init__(self) -> None:
        self.cards = {
            "PSI-102": {
                "id": "PSI-102",
                "name": "SAME",
                "acronym": "SAME",
                "family": "ISS aerosol",
                "familyId": "aerosol",
                "score": 96,
                "reason": "Primary detection dataset.",
                "summary": "Smoke aerosol measurement.",
                "fuels": ["Teflon", "Kapton"],
                "oxygenMin": None,
                "oxygenMax": None,
                "oxygen": "no oxygen column",
                "runCount": 8,
                "doi": "",
                "blurb": "",
                "note": None,
            },
            "PSI-69": {
                "id": "PSI-69",
                "name": "FLEX",
                "acronym": "FLEX",
                "family": "MDCA droplets",
                "familyId": "mdca",
                "score": 92,
                "reason": "Suppression dataset.",
                "summary": "Droplets.",
                "fuels": ["Methanol"],
                "oxygenMin": 12.0,
                "oxygenMax": 34.0,
                "oxygen": "12.0–34.0% oxygen",
                "runCount": 274,
                "doi": "10.1/flex",
                "blurb": "",
                "note": None,
            },
        }

    def investigation_names(self):
        return {key: card["name"] for key, card in self.cards.items()}

    def list_investigations(self):
        return list(self.cards.values())

    def get_investigation(self, psi_id):
        return self.cards.get(psi_id)

    def rank_investigations(self, scenario, role, limit):
        ranked = sorted(self.cards.values(), key=lambda card: -card["score"])[:limit]
        return {
            "scenario": {"id": scenario, "label": "ISS cabin (21% O2, 14.7 psi)", "caveat": "Station air."},
            "ranked": ranked,
        }

    def graph_neighbors(self, psi_id, depth=1):
        card = self.cards.get(psi_id)
        if not card:
            return None
        links = [
            {"relation": "MENTIONS", "id": f"fuel:soot@{psi_id}", "type": "fuel", "label": "soot"},
            {"relation": "USED_HARDWARE", "id": f"hardware:dusttrak@{psi_id}", "type": "hardware", "label": "sensor:dusttrak@PSI-102"},
        ]
        links.extend(
            {
                "relation": "RELATED_TO",
                "id": f"extra-{index}",
                "type": "phenomenon",
                "label": f"node-{index}",
            }
            for index in range(70)
        )
        if int(depth or 1) >= 2:
            links.extend(list(getattr(self, "hop2", []) or []))
        return {"id": psi_id, "name": card["name"], "family": card["family"], "links": links, "peers": card.get("peers") or []}

    def local_search(self, query, limit):
        return {
            "hits": [
                {
                    "chunkId": "PSI-102/paper/p002",
                    "preview": "Soot particle size distributions are larger in microgravity.",
                    "page": 2,
                    "source": "ices-paper",
                }
            ]
        }

    def run(self, query, **params):
        text = query or ""
        if "Suppressant" in text:
            if getattr(self, "suppressant_error", False):
                raise RuntimeError("suppressant down")
            return list(getattr(self, "suppressant_rows", []) or [])
        if "MEASURED_WITH" in text:
            return list(getattr(self, "sensor_rows", []) or [])
        if "Community" in text:
            return list(getattr(self, "communities", []) or [])
        if "Fuel" in text or "Phenomenon" in text:
            return list(getattr(self, "heatmap_rows", []) or [])
        if "documentCount" in text:
            return list(getattr(self, "count_rows", []) or [])
        if "Chunk" in text:
            return list(getattr(self, "chunk_counts", []) or [])
        return []


def test_profile_hides_missing_oxygen() -> None:
    spec = compose_profile(Store(), "PSI-102")
    names = [block["component"] for block in spec["blocks"]]
    assert spec["intent"] == "profile"
    assert "GaugeO2" not in names
    assert missing("oxygen") in spec["blocks"]
    assert "EvidencePanel" in names


def test_profile_gauge_uses_catalog_bounds() -> None:
    spec = compose_profile(Store(), "PSI-69")
    gauge = next(block for block in spec["blocks"] if block["component"] == "GaugeO2")
    assert gauge["data"]["min"] == 12.0
    assert gauge["data"]["max"] == 34.0
    assert gauge["data"]["missing"] is False


def test_graph_drops_mentions_and_caps_nodes() -> None:
    spec = compose_graph(Store(), "PSI-102")
    graph = spec["blocks"][0]["data"]
    assert len(graph["nodes"]) <= 60
    assert len(graph["edges"]) <= 120
    assert all(edge["type"] != "MENTIONS" for edge in graph["edges"])
    hardware = next(node for node in graph["nodes"] if node["type"] == "hardware")
    assert hardware["label"] == "dusttrak"
    assert "sensor:" not in hardware["label"]


def test_scenario_briefing_keeps_rank_order() -> None:
    spec = compose(Store(), "What should a crew read for the ISS?")
    assert spec["intent"] == "scenario"
    briefing = spec["blocks"][0]["data"]
    assert briefing["investigations"][0]["id"] == "PSI-102"
    assert any(block["component"] == "SourceBadge" for block in spec["blocks"])
    assert len(spec["followups"]) <= 3


def test_explore_applies_family_filter() -> None:
    spec = compose(Store(), "Liệt kê investigation", {"filters": [{"kind": "family", "id": "aerosol"}]})
    cards = [block for block in spec["blocks"] if block["component"] == "InvestigationCard"]
    assert [card["data"]["id"] for card in cards] == ["PSI-102"]


def test_compare_marks_missing_oxygen() -> None:
    spec = compose(Store(), "Compare SAME vs FLEX", None)
    # named ids come from aliases inside the question via the real mention parser,
    # which needs investigation_names. SAME and FLEX are aliases.
    table = next(block["data"] for block in spec["blocks"] if block["component"] == "CompareTable")
    oxygen = next(row for row in table["rows"] if row["field"] == "oxygen")
    assert "—" in oxygen["values"]
    assert True in oxygen["missing"]
    assert False in oxygen["missing"]
    assert "12.0–34.0% oxygen" in oxygen["values"]


def test_qa_text_is_the_chunk_quote() -> None:
    spec = compose(Store(), "why is soot larger in microgravity?")
    assert spec["intent"] == "qa"
    assert "larger in microgravity" in spec["text"]
    assert spec["blocks"][0]["component"] == "EvidencePanel"
    chunk = spec["blocks"][0]["data"]["chunks"][0]
    assert chunk["documentTitle"] == "ices-paper"
    assert chunk["figures"] == []
    assert len(spec["blocks"][0]["data"]["chunks"]) <= 5
    assert len(spec["text"].split()) <= 120


def test_gateway_briefing_uses_moon_scores() -> None:
    spec = compose(Store(), "Briefing for Lunar Gateway")
    assert spec["intent"] == "scenario"
    assert spec["filters"][0]["id"] == "moon"


def test_topic_block_shape() -> None:
    store = Store()
    store.communities = [
        {
            "id": "community:L2:0",
            "title": "ISS smoke",
            "summary": "Theme only 21% oxygen ignore",
            "level": 2,
            "size": 10,
            "parent": None,
        },
        {
            "id": "community:L1:4",
            "title": "SAME aerosols",
            "summary": "Dust",
            "level": 1,
            "size": 4,
            "parent": "community:L2:0",
        },
        {
            "id": "community:L0:9",
            "title": "DustTrak",
            "summary": "Sensor cluster",
            "level": 0,
            "size": 2,
            "parent": "community:L1:4",
        },
    ]
    spec = compose(
        store,
        "Tổng quan chủ đề cháy trên ISS",
        {"filters": [{"kind": "community", "id": "community:L1:4"}]},
    )
    assert spec["intent"] == "topic"
    block = next(item for item in spec["blocks"] if item["component"] == "TopicDrilldown")
    data = block["data"]
    by_id = {item["id"]: item for item in data["communities"]}
    assert set(by_id) == {"community:L1:4", "community:L0:9"}
    child = by_id["community:L0:9"]
    assert child["title"] == "DustTrak"
    assert child["summary"] == "Sensor cluster"
    assert child["level"] == 0
    assert child["parent"] == "community:L1:4"
    assert "psiIds" not in child
    assert "chunkIds" not in child
    assert [item["id"] for item in data["breadcrumb"]] == ["community:L2:0", "community:L1:4"]
    assert data["levels"] == [0, 1, 2]
    assert "GaugeO2" not in [item["component"] for item in spec["blocks"]]
    assert "21%" not in json.dumps(spec)
    assert "oxygenMin" not in json.dumps(block)


def test_coverage_does_not_invent_oxygen(monkeypatch, tmp_path) -> None:
    from app import viz

    monkeypatch.setattr(viz, "COVERAGE_PATH", tmp_path / "viz_coverage.json")
    store = Store()
    store.chunk_counts = [{"source": "PSI-102", "n": 3}]
    spec = compose(store, "Dữ liệu phủ tới đâu")
    assert spec["intent"] == "coverage"
    chart = next(block for block in spec["blocks"] if block["component"] == "BarChart")
    assert chart["data"]["series"] == [{"label": "PSI-102", "value": 3}]
    assert chart["data"]["unit"] == "chunk"
    assert "không phải điểm mô hình" in chart["data"]["caption"]
    assert "HeatMap" in chart["data"]["caption"]
    heatmap = next(block for block in spec["blocks"] if block["component"] == "HeatMap")
    assert heatmap["data"]["cells"] == []
    assert "chia cho số chunk" in heatmap["data"]["caption"]
    table = next(block for block in spec["blocks"] if block["component"] == "DataTable")
    oxygen = next(row for row in table["data"]["rows"] if row[0] == "oxygen")
    assert oxygen == ["oxygen", 1, 1, 2]
    dumped = json.dumps(spec)
    assert "12.0" not in dumped
    assert "34" not in dumped
    assert "21.5" not in dumped
    assert "1251" not in dumped
    assert (tmp_path / "viz_coverage.json").is_file()


def test_extract_csv_blank_oxygen_not_zero() -> None:
    text = extract_csv(Store())
    assert text.startswith("# warning:")
    assert "sampleForm covered 0/2 (<50%)" in text.splitlines()[0]
    assert "measurements covered 0/2 (<50%)" in text.splitlines()[0]
    assert "documentCount covered 0/2 (<50%)" in text.splitlines()[0]
    assert "chunkCount covered 0/2 (<50%)" in text.splitlines()[0]
    assert "oxygenMin covered" not in text.splitlines()[0]
    body = "\n".join(line for line in text.splitlines() if not line.startswith("#"))
    rows = list(csv.DictReader(io.StringIO(body)))
    same = next(row for row in rows if row["acronym"] == "SAME")
    flex = next(row for row in rows if row["acronym"] == "FLEX")
    assert same["oxygenMin"] == ""
    assert same["oxygenMax"] == ""
    assert same["runCount"] == "8"
    assert same["sampleForm"] == ""
    assert same["measurements"] == ""
    assert same["documentCount"] == ""
    assert same["chunkCount"] == ""
    assert flex["oxygenMin"] == "12"
    assert flex["oxygenMax"] == "34"
    spec = compose(Store(), "Xuất bảng dữ liệu")
    assert spec["intent"] == "extract"
    table = next(block["data"] for block in spec["blocks"] if block["component"] == "DataTable")
    header = table["columns"]
    oxygen_min = header.index("oxygenMin")
    same_row = next(row for row in table["rows"] if row[0] == "SAME")
    assert same_row[oxygen_min] == ""
    assert "0" not in (same_row[oxygen_min], same_row[header.index("oxygenMax")])
    assert header[-3:] == ["measurements", "documentCount", "chunkCount"]
    assert same_row[header.index("measurements")] == ""
    assert same_row[header.index("documentCount")] == ""
    assert same_row[header.index("chunkCount")] == ""


def test_sensor_matrix_uses_only_edges() -> None:
    store = Store()
    store.sensor_rows = [
        {
            "sensorId": "sensor:dusttrak@PSI-47",
            "label": "sensor:dusttrak@PSI-47",
            "psiId": "PSI-47",
            "n": 2,
        },
        {"sensorId": "sensor:ptrak@PSI-47", "label": "P-Trak", "psiId": "PSI-102", "n": 1},
    ]
    spec = compose(store, "Cảm biến nào phát hiện khói tốt")
    assert spec["intent"] == "sensor"
    block = next(item for item in spec["blocks"] if item["component"] == "SensorMatrix")
    matrix = block["data"]
    assert matrix["investigations"] == ["PSI-47", "PSI-102"]
    assert "PSI-69" not in matrix["investigations"]
    dust = next(sensor for sensor in matrix["sensors"] if sensor["id"] == "sensor:dusttrak@PSI-47")
    assert dust["label"] == "dusttrak"
    assert {(cell["sensor"], cell["investigation"], cell["value"]) for cell in matrix["cells"]} == {
        ("sensor:dusttrak@PSI-47", "PSI-47", 2),
        ("sensor:ptrak@PSI-47", "PSI-102", 1),
    }
    assert "sensor:dusttrak@PSI-47" in json.dumps(block["evidence"])
    assert "mỏng" in matrix["note"]
    scoped = Store()
    scoped.sensor_rows = [
        {
            "sensorId": "sensor:sensor-dusttrak-psi-47@PSI-47",
            "label": "sensor:sensor-dusttrak-psi-47@PSI-47",
            "psiId": "PSI-47",
            "n": 2,
        }
    ]
    scoped_spec = compose(scoped, "Cảm biến nào phát hiện khói tốt")
    scoped_block = next(item for item in scoped_spec["blocks"] if item["component"] == "SensorMatrix")
    scoped_dust = scoped_block["data"]["sensors"][0]
    assert scoped_dust["label"] == "dusttrak"
    assert scoped_dust["id"] == "sensor:sensor-dusttrak-psi-47@PSI-47"
    assert "sensor:sensor-dusttrak-psi-47@PSI-47" in json.dumps(scoped_block["evidence"])
    dumped = json.dumps(spec)
    assert "103" not in dumped
    assert "45" not in dumped
    assert "Chất dập cháy" not in dumped
    assert "Hiện tượng extinction" not in dumped


def test_profile_flow_steps_only_when_flow_exists() -> None:
    store = Store()
    store.cards["PSI-102"]["flow"] = "no airflow column in the tables held here"
    store.cards["PSI-102"]["summary"] = "Step one then step two from a community summary."
    bare = compose_profile(store, "PSI-102")
    assert all(block["component"] != "FlowSteps" for block in bare["blocks"])
    store.cards["PSI-69"]["flow"] = "5–10 cm/s"
    spec = compose_profile(store, "PSI-69")
    flows = [block for block in spec["blocks"] if block["component"] == "FlowSteps"]
    assert len(flows) == 1
    assert flows[0]["data"]["steps"] == [{"title": "Dòng khí", "text": "5–10 cm/s"}]


def test_coverage_heatmap_normalizes_by_source(monkeypatch, tmp_path) -> None:
    from app import viz

    monkeypatch.setattr(viz, "COVERAGE_PATH", tmp_path / "viz_coverage.json")
    store = Store()
    store.chunk_counts = [
        {"source": "ACME_common", "n": 1000},
        {"source": "small", "n": 10},
    ]
    store.heatmap_rows = [
        {
            "fuelId": "fuel:pmma@PSI-25",
            "fuelLabel": "PMMA",
            "phenomenonId": "phenomenon:soot@PSI-25",
            "phenomenonLabel": "soot",
            "source": "ACME_common",
            "n": 1000,
        },
        {
            "fuelId": "fuel:PMMA@PSI-99",
            "fuelLabel": "pmma",
            "phenomenonId": "phenomenon:Soot@PSI-99",
            "phenomenonLabel": "Soot",
            "source": "small",
            "n": 10,
        },
    ]
    spec = compose(store, "Dữ liệu phủ tới đâu")
    assert spec["intent"] == "coverage"
    names = [block["component"] for block in spec["blocks"]]
    assert "BarChart" in names and "HeatMap" in names and "DataTable" in names
    chart = next(block["data"] for block in spec["blocks"] if block["component"] == "BarChart")
    assert {"label": "ACME_common", "value": 1000} in chart["series"]
    heatmap = next(block for block in spec["blocks"] if block["component"] == "HeatMap")
    assert heatmap["data"]["cells"] == [{"x": "soot", "y": "PMMA", "v": 2.0}]
    assert heatmap["data"]["cells"][0]["v"] < 10
    assert "chia cho số chunk" in heatmap["data"]["caption"]
    assert "12" in heatmap["data"]["caption"]
    evidence = json.dumps(heatmap["evidence"])
    assert "fuel:pmma@PSI-25" in evidence
    assert "fuel:PMMA@PSI-99" in evidence
    assert "phenomenon:soot@PSI-25" in evidence
    english = compose(store, "Is coverage enough to train model")
    assert english["intent"] == "coverage"
    assert any(block["component"] == "HeatMap" for block in english["blocks"])


def test_extract_agent_adds_linked_counts() -> None:
    store = Store()
    store.cards["PSI-69"]["measurements"] = ["flame temperature"]
    store.cards["PSI-102"]["oxygenMin"] = None
    store.cards["PSI-102"]["oxygenMax"] = None
    store.count_rows = [
        {"psiId": "PSI-69", "documentCount": 4, "chunkCount": 11},
        {"psiId": "PSI-102", "documentCount": 0, "chunkCount": 2},
    ]
    spec = compose(store, "Xuất csv")
    assert spec["intent"] == "extract"
    table = next(block["data"] for block in spec["blocks"] if block["component"] == "DataTable")
    header = table["columns"]
    assert header[-3:] == ["measurements", "documentCount", "chunkCount"]
    same = next(row for row in table["rows"] if row[0] == "SAME")
    flex = next(row for row in table["rows"] if row[0] == "FLEX")
    assert same[header.index("oxygenMin")] == ""
    assert same[header.index("oxygenMax")] == ""
    assert same[header.index("measurements")] == ""
    assert same[header.index("documentCount")] == "0"
    assert same[header.index("chunkCount")] == "2"
    assert flex[header.index("oxygenMin")] == "12"
    assert flex[header.index("measurements")] == "flame temperature"
    assert flex[header.index("documentCount")] == "4"
    text = extract_csv(store)
    warning = text.splitlines()[0]
    assert "sampleForm covered 0/2 (<50%)" in warning
    assert "measurements covered" not in warning
    assert "oxygenMin covered" not in warning
    assert text.splitlines()[1].endswith("measurements,documentCount,chunkCount")


def test_sensor_rollup_cleans_label_and_keeps_raw_id() -> None:
    store = Store()
    store.sensor_rows = [
        {
            "sensorId": "sensor:dusttrak@PSI-47",
            "label": "sensor:dusttrak@PSI-47",
            "psiId": "PSI-47",
            "n": 2,
        },
        {"sensorId": "sensor:DustTrak@PSI-102", "label": "DustTrak", "psiId": "PSI-102", "n": 5},
        {"sensorId": "sensor:cir@PSI-69", "label": "CIR", "psiId": "PSI-69", "n": 4},
        {"sensorId": "sensor:cir-bay@PSI-70", "label": "CIR", "psiId": "PSI-70", "n": 1},
    ]
    store.suppressant_rows = [{"n": 7}]
    spec = compose(store, "Which sensor detects smoke")
    block = next(item for item in spec["blocks"] if item["component"] == "SensorMatrix")
    matrix = block["data"]
    dust = next(sensor for sensor in matrix["sensors"] if "dusttrak" in sensor["id"].lower())
    assert dust["label"] == "DustTrak"
    assert dust["id"] == "sensor:DustTrak@PSI-102"
    assert set(dust["rawIds"]) == {"sensor:dusttrak@PSI-47", "sensor:DustTrak@PSI-102"}
    cir = next(sensor for sensor in matrix["sensors"] if sensor["id"] == "sensor:cir@PSI-69")
    bay = next(sensor for sensor in matrix["sensors"] if sensor["id"] == "sensor:cir-bay@PSI-70")
    assert cir["label"] == "CIR"
    assert bay["label"] != "CIR"
    assert "sensor:cir-bay@PSI-70" in bay["label"]
    assert {(cell["sensor"], cell["investigation"], cell["value"]) for cell in matrix["cells"]} == {
        ("sensor:DustTrak@PSI-102", "PSI-47", 2),
        ("sensor:DustTrak@PSI-102", "PSI-102", 5),
        ("sensor:cir@PSI-69", "PSI-69", 4),
        ("sensor:cir-bay@PSI-70", "PSI-70", 1),
    }
    evidence = json.dumps(block["evidence"])
    assert "sensor:dusttrak@PSI-47" in evidence
    assert "sensor:DustTrak@PSI-102" in evidence
    assert "sensor:cir@PSI-69" in evidence
    assert "sensor:cir-bay@PSI-70" in evidence
    assert "(7 node)" in matrix["note"]
    assert "103" not in json.dumps(spec)
    failed = Store()
    failed.sensor_rows = store.sensor_rows
    failed.suppressant_error = True
    quiet = compose(failed, "Cảm biến dập cháy")
    note = next(item["data"]["note"] for item in quiet["blocks"] if item["component"] == "SensorMatrix")
    assert "mỏng" in note
    assert "node)" not in note


def test_snapshot_short_circuits(monkeypatch, tmp_path) -> None:
    from app import viz

    path = tmp_path / "viz_snapshots.json"
    path.write_text('{"hello": {"version": "1.0", "intent": "explore", "title": "cached", "blocks": []}}', encoding="utf-8")
    monkeypatch.setattr(viz, "SNAPSHOT_PATH", path)
    spec = compose(Store(), "Hello")
    assert spec["title"] == "cached"


def test_iss_relevance_classifies_as_scenario() -> None:
    question = "Thí nghiệm nào liên quan nhất cho trạm ISS?"
    assert classify(question, [], "iss") == "scenario"
    assert classify("top experiments for the moon", [], "moon") == "scenario"
    assert classify("xếp hạng cho bộ đồ không gian", [], "suit") == "scenario"
    spec = compose(Store(), question)
    assert spec["intent"] == "scenario"
    briefing = spec["blocks"][0]
    assert briefing["component"] == "ScenarioBriefing"
    store = Store()
    top = max(store.cards.values(), key=lambda card: card["score"])["id"]
    assert briefing["data"]["investigations"][0]["id"] == top
    assert "gaps" not in briefing["data"]


def test_graph_question_stays_graph() -> None:
    assert classify("Đồ thị hàng xóm của PSI-102", ["PSI-102"], "iss") == "graph"
    assert classify("PSI-102 liên quan tới ISS", ["PSI-102"], "iss") == "graph"
    assert classify("node neighbors of FLEX", ["PSI-69"], None) == "graph"
    spec = compose(Store(), "Đồ thị hàng xóm của PSI-102")
    assert spec["intent"] == "graph"
    assert spec["blocks"][0]["component"] == "NodeGraph"


def test_explore_sorts_by_scenario_relevance() -> None:
    store = Store()

    def rank(scenario, role, limit):
        assert scenario == "iss"
        return {
            "scenario": {"id": "iss", "label": "ISS"},
            "ranked": [
                {"id": "PSI-69", "name": "FLEX", "score": 80},
                {"id": "PSI-102", "name": "SAME", "score": 10},
            ][:limit],
        }

    store.rank_investigations = rank
    spec = compose(
        store,
        "Liệt kê investigation",
        {"filters": [{"kind": "scenario", "id": "iss", "label": "ISS"}]},
    )
    assert spec["intent"] == "explore"
    cards = [block for block in spec["blocks"] if block["component"] == "InvestigationCard"]
    assert [card["data"]["id"] for card in cards] == ["PSI-69", "PSI-102"]
    assert cards[0]["data"]["relevance"] == 80
    assert cards[1]["data"]["relevance"] == 10
    assert 0 not in (cards[0]["data"]["relevance"], cards[1]["data"]["relevance"])


def test_depth_two_is_second_hop_not_family_peers() -> None:
    store = Store()
    store.cards["PSI-102"]["peers"] = [{"id": "PSI-69", "name": "FLEX"}]

    def graph_neighbors(psi_id, depth=1):
        links = [
            {
                "relation": "USED_HARDWARE",
                "id": "hardware:dusttrak@PSI-102",
                "type": "hardware",
                "label": "DustTrak",
                "fromId": psi_id,
            }
        ]
        if int(depth or 1) >= 2:
            links.append(
                {
                    "relation": "STUDIED",
                    "id": "phenomenon:smoke@PSI-102",
                    "type": "phenomenon",
                    "label": "smoke",
                    "fromId": "hardware:dusttrak@PSI-102",
                }
            )
        return {
            "id": psi_id,
            "name": "SAME",
            "links": links,
            "peers": store.cards["PSI-102"]["peers"],
        }

    store.graph_neighbors = graph_neighbors
    spec = compose_graph(store, "PSI-102", depth=2)
    graph = spec["blocks"][0]["data"]
    edges = graph["edges"]
    smoke = next(edge for edge in edges if edge["type"] == "STUDIED")
    assert smoke["from"] == "hardware:dusttrak@PSI-102"
    assert smoke["to"] == "phenomenon:smoke@PSI-102"
    assert "STUDIED" in smoke["explanation"]
    assert "smoke" in smoke["explanation"]
    assert "DustTrak" in smoke["explanation"]
    assert all(edge["type"] != "IN_FAMILY" for edge in edges)
    assert all(edge["type"] != "MENTIONS" for edge in edges)
    assert all("PSI-69" != edge["to"] for edge in edges)
    assert all(node["weight"] == 1 for node in graph["nodes"])
    assert len(graph["nodes"]) <= 60
    assert len(graph["edges"]) <= 120


def test_graph_fuel_filter_keeps_matching_nodes() -> None:
    store = Store()

    def graph_neighbors(psi_id, depth=1):
        return {
            "id": psi_id,
            "name": "SAME",
            "links": [
                {"relation": "TESTED_FUEL", "id": "fuel:pmma@PSI-102", "type": "fuel", "label": "PMMA", "fromId": psi_id},
                {
                    "relation": "USED_HARDWARE",
                    "id": "hardware:dusttrak@PSI-102",
                    "type": "hardware",
                    "label": "DustTrak",
                    "fromId": psi_id,
                },
            ],
            "peers": [{"id": "PSI-69", "name": "FLEX"}],
        }

    store.graph_neighbors = graph_neighbors
    spec = compose(
        store,
        "Đồ thị quanh PSI-102",
        {"filters": [{"kind": "fuel", "label": "PMMA"}]},
    )
    assert spec["intent"] == "graph"
    labels = {node["label"] for node in spec["blocks"][0]["data"]["nodes"]}
    assert "PMMA" in labels
    assert "DustTrak" not in labels
    assert any(item.get("kind") == "fuel" for item in spec["filters"])
    assert all(edge["type"] != "IN_FAMILY" for edge in spec["blocks"][0]["data"]["edges"])


def test_profile_uses_catalog_sources_hardware_and_counts() -> None:
    store = Store()
    store.cards["PSI-69"]["sources"] = ["data/psi/PSI-69/table.csv"]
    store.cards["PSI-69"]["hardware"] = "MDCA"
    store.cards["PSI-69"]["oxygenNote"] = "Measured oxygen concentration"
    store.count_rows = [{"psiId": "PSI-69", "documentCount": 2, "chunkCount": 0}]
    spec = compose_profile(store, "PSI-69")
    header = next(block for block in spec["blocks"] if block["component"] == "ProfileHeader")
    assert header["data"]["sources"] == ["data/psi/PSI-69/table.csv"]
    gauge = next(block for block in spec["blocks"] if block["component"] == "GaugeO2")
    assert gauge["evidence"] == [{"kind": "field", "ref": "Investigation.oxygenMin"}]
    assert gauge["data"]["note"] == "Measured oxygen concentration"
    hardware = next(
        block
        for block in spec["blocks"]
        if block["component"] == "ChipList" and block["data"]["title"] == "Thiết bị"
    )
    assert hardware["data"]["items"] == [{"label": "MDCA"}]
    stats = {
        block["data"]["label"]: block["data"]["value"]
        for block in spec["blocks"]
        if block["component"] == "StatCard"
    }
    assert stats["Số tài liệu"] == 2
    assert stats["Số chunk"] == 0
    bare = compose_profile(Store(), "PSI-102")
    bare_header = next(block for block in bare["blocks"] if block["component"] == "ProfileHeader")
    assert bare_header["data"]["sources"] is None
    assert all(
        block["component"] != "ChipList" or block["data"]["title"] != "Thiết bị" for block in bare["blocks"]
    )
    assert "Số tài liệu" not in {
        block["data"].get("label") for block in bare["blocks"] if block["component"] == "StatCard"
    }


def test_compare_rows_only_when_payload_has_them() -> None:
    store = Store()
    store.cards["PSI-69"]["hardware"] = "MDCA"
    store.cards["PSI-102"]["chunkCount"] = 0
    spec = compose(store, "Compare SAME vs FLEX")
    table = next(block["data"] for block in spec["blocks"] if block["component"] == "CompareTable")
    assert len(table["columns"]) <= 3
    fields = [row["field"] for row in table["rows"]]
    assert "facility" not in fields
    assert "phenomenon" not in fields
    hardware = next(row for row in table["rows"] if row["field"] == "hardware")
    oxygen = next(row for row in table["rows"] if row["field"] == "oxygen")
    chunks = next(row for row in table["rows"] if row["field"] == "chunkCount")
    by_id = dict(zip(table["columns"], hardware["values"]))
    missing = dict(zip(table["columns"], hardware["missing"]))
    assert by_id["PSI-69"] == "MDCA"
    assert by_id["PSI-102"] == "—"
    assert missing["PSI-102"] is True
    assert missing["PSI-69"] is False
    assert "—" in oxygen["values"]
    assert True in oxygen["missing"]
    assert "12.0–34.0% oxygen" in oxygen["values"]
    chunk_by_id = dict(zip(table["columns"], chunks["values"]))
    chunk_missing = dict(zip(table["columns"], chunks["missing"]))
    assert chunk_by_id["PSI-102"] == 0
    assert chunk_missing["PSI-102"] is False
    assert chunk_by_id["PSI-69"] == "—"
    assert chunk_missing["PSI-69"] is True


def test_topic_psi_ids_come_from_the_node() -> None:
    store = Store()
    store.communities = [
        {
            "id": "community:L2:0",
            "title": "ISS smoke",
            "summary": "Theme",
            "level": 2,
            "size": 3,
            "parent": None,
            "psiIds": ["inv:PSI-102", "PSI-69"],
        }
    ]
    spec = compose(store, "Bản đồ chủ đề")
    community = spec["blocks"][0]["data"]["communities"][0]
    assert community["psiIds"] == ["PSI-102", "PSI-69"]
    assert "chunkIds" not in community


def test_coverage_counts_section_fields(monkeypatch, tmp_path) -> None:
    from app import viz

    monkeypatch.setattr(viz, "COVERAGE_PATH", tmp_path / "viz_coverage.json")
    spec = compose(Store(), "Độ phủ dữ liệu")
    table = next(block for block in spec["blocks"] if block["component"] == "DataTable")
    by_name = {row[0]: row for row in table["data"]["rows"]}
    assert by_name["oxygen"] == ["oxygen", 1, 1, 2]
    assert by_name["summary"] == ["summary", 2, 0, 2]
    assert by_name["oxygenMin"] == ["oxygenMin", 1, 1, 2]
    assert by_name["oxygenMax"] == ["oxygenMax", 1, 1, 2]
    assert by_name["oxygenNote"] == ["oxygenNote", 0, 2, 2]
    assert by_name["sources"] == ["sources", 0, 2, 2]
    assert by_name["runCount"] == ["runCount", 2, 0, 2]
    dumped = json.dumps(spec)
    assert "21.5" not in dumped
    assert "22.1" not in dumped


def test_evidence_prefers_document_title() -> None:
    store = Store()

    def local_search(query, limit):
        return {
            "hits": [
                {
                    "chunkId": "c1",
                    "preview": "The table lists the oxygen bounds beside the run id.",
                    "page": 4,
                    "source": "data/psi/report.pdf",
                    "documentTitle": "Saffire-II report",
                }
            ]
        }

    store.local_search = local_search
    spec = compose(store, "trang nào nói về oxy?")
    assert spec["intent"] == "evidence"
    chunk = spec["blocks"][0]["data"]["chunks"][0]
    assert chunk["documentTitle"] == "Saffire-II report"
    assert chunk["source"] == "data/psi/report.pdf"
    assert chunk["figures"] == []
    assert len(spec["blocks"][0]["data"]["chunks"]) <= 5


def test_scenario_context_passes_notes_without_invented_gaps() -> None:
    store = Store()

    def scenario_context(scenario):
        assert scenario == "iss"
        return {
            "scenario": {"label": "ISS cabin", "caveat": "Station air."},
            "notes": [{"label": "Artemis", "summary": "Smoke alarm history.", "url": "https://example.test"}],
            "phenomena": [{"id": "phenomenon:smoke", "label": "smoke"}],
        }

    store.scenario_context = scenario_context
    spec = compose(store, "Thí nghiệm nào liên quan nhất cho trạm ISS?")
    data = spec["blocks"][0]["data"]
    assert data["context"][0]["summary"] == "Smoke alarm history."
    assert data["risks"] == [{"id": "phenomenon:smoke", "label": "smoke"}]
    assert "sensors" not in data
    assert "gaps" not in data
    top = max(store.cards.values(), key=lambda card: card["score"])["id"]
    assert data["investigations"][0]["id"] == top


def test_sensor_extinction_block_uses_returned_rows() -> None:
    store = Store()
    store.sensor_rows = [
        {"sensorId": "sensor:dusttrak@PSI-47", "label": "DustTrak", "psiId": "PSI-47", "n": 2}
    ]

    def run(query, **params):
        text = query or ""
        if "extinction" in text and "Chunk" in text:
            return [
                {
                    "chunkId": "c-ext",
                    "preview": "Extinction occurred after the mist.",
                    "page": 3,
                    "pages_span": [3, 3],
                    "source": "psi-note",
                }
            ]
        if "extinction" in text:
            return [{"id": "phenomenon:extinction@PSI-69", "label": "extinction"}]
        if "Suppressant" in text and "count" not in text.lower():
            return [{"id": "suppressant:water-mist@PSI-69", "label": "water mist"}]
        return Store.run(store, query, **params)

    store.run = run
    spec = compose(store, "Cảm biến nào phát hiện khói tốt")
    titles = [
        block["data"].get("title")
        for block in spec["blocks"]
        if block["component"] == "ChipList"
    ]
    assert "Chất dập cháy" in titles
    assert "Hiện tượng extinction" in titles
    evidence = next(block for block in spec["blocks"] if block["component"] == "EvidencePanel")
    assert evidence["data"]["chunks"][0]["text"] == "Extinction occurred after the mist."
    assert evidence["data"]["chunks"][0]["figures"] == []
    assert evidence["data"]["chunks"][0]["pages_span"] == [3, 3]
    assert evidence["evidence"][0]["chunkId"] == "c-ext"
    assert "103" not in json.dumps(spec)


def test_alias_rollup_does_not_merge_ethylene_with_c2h4() -> None:
    from app.viz import _rollup_key

    assert _rollup_key("fuel:ethylene@PSI-25", "Ethylene") != _rollup_key("fuel:c2h4@PSI-25", "C2H4")


def test_profile_and_graph_errors_stay_missing(monkeypatch) -> None:
    from app import main

    class Boom:
        def investigation_names(self):
            return {"PSI-69": "FLEX"}

        def get_investigation(self, psi_id):
            raise RuntimeError("aura down")

        def graph_neighbors(self, psi_id, depth=1):
            raise RuntimeError("aura down")

    monkeypatch.setattr(main, "get_store", lambda: Boom())
    profile = main.viz_profile("PSI-69")
    graph = main.viz_graph("PSI-102", depth=2)
    assert profile["blocks"][0]["component"] == "MissingData"
    assert graph["blocks"][0]["component"] == "MissingData"


def test_viz_reads_are_limited_and_timed_out() -> None:
    from neo4j import Query

    from app.graph import GRAPH_READ_LIMIT, QUERY_TIMEOUT_SECONDS, GraphStore

    store = GraphStore.__new__(GraphStore)
    calls = []

    def run(query, **params):
        calls.append((query, params))
        if len(calls) == 1:
            return [
                {
                    "relation": "USED_HARDWARE",
                    "id": "hardware:dusttrak@PSI-102",
                    "type": "Hardware",
                    "label": "DustTrak",
                    "fromId": "PSI-102",
                }
            ]
        return []

    store.run = run
    store.get_investigation = lambda psi_id: {"id": psi_id, "name": "SAME", "family": "ISS", "peers": []}
    links = store.graph_neighbors("PSI-102", depth=2)["links"]
    assert links[0]["id"] == "hardware:dusttrak@PSI-102"
    assert links[0]["fromId"] == "PSI-102"
    assert len(calls) == 2
    for query, params in calls:
        assert "LIMIT $limit" in query
        assert params["limit"] == GRAPH_READ_LIMIT
        assert "MENTIONS" not in params["rels"]
    assert calls[1][1]["ids"] == ["hardware:dusttrak@PSI-102"]

    captured = {}

    def run_global(query, **params):
        captured["query"] = query
        captured["params"] = params
        return []

    store.run = run_global
    result = store.global_search("smoke", limit=3)
    assert "LIMIT $limit" in captured["query"]
    assert captured["params"]["limit"] >= 1
    assert result["degraded"] is True

    seen = {}

    class Session:
        def run(self, query, params=None, **kwargs):
            seen["query"] = query
            seen["params"] = params
            return []

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    timed = GraphStore.__new__(GraphStore)
    timed.database = "neo4j"
    timed.session = lambda: Session()
    assert timed.run("MATCH (n) RETURN n LIMIT 1") == []
    assert isinstance(seen["query"], Query)
    assert seen["query"].timeout == QUERY_TIMEOUT_SECONDS


def test_format_flow_does_not_double_a_unit() -> None:
    from app.knowledge import format_flow, oxygen_note

    assert format_flow("5–10") == "5–10 cm/s"
    assert format_flow("20") == "20 cm/s"
    assert format_flow("5–10 cm/s") == "5–10 cm/s"
    assert format_flow("no airflow column in the tables held here") == "no airflow column in the tables held here"
    assert oxygen_note({"id": "PSI-159", "oxygenMax": 85}) is None
    assert oxygen_note({"id": "PSI-159", "oxygenNote": "Stored on the node."}) == "Stored on the node."


def test_profile_oxygen_figure_comes_from_chunk_text() -> None:
    store = Store()

    def run(query, **params):
        if "oxygen" in (query or "").lower():
            return [
                {
                    "chunkId": "c-ox",
                    "text": "The measured oxygen was 21.5–22.1% during the burn.",
                    "page": 4,
                    "pages_span": [4, 5],
                }
            ]
        return Store.run(store, query, **params)

    store.run = run
    spec = compose_profile(store, "PSI-102")
    gauge = next(block for block in spec["blocks"] if block["component"] == "GaugeO2")
    chunk_text = "The measured oxygen was 21.5–22.1% during the burn."
    assert gauge["data"]["min"] == 21.5
    assert gauge["data"]["max"] == 22.1
    assert gauge["data"]["note"] in chunk_text
    assert "21.5" in gauge["data"]["note"] and "22.1" in gauge["data"]["note"]
    assert gauge["evidence"][0]["chunkId"] == "c-ox"
    assert gauge["evidence"][0]["kind"] == "chunk"
    assert gauge["evidence"][0]["pages_span"] == [4, 5]
    badge = next(block for block in spec["blocks"] if block["component"] == "SourceBadge")
    assert badge["data"]["kind"] == "chunk"
    assert badge["data"]["chunkId"] == "c-ox"
    assert missing("oxygen") not in spec["blocks"]

    separate = Store()

    def run_separate(query, **params):
        if "oxygen" in (query or "").lower():
            return [{"chunkId": "c-two", "text": "21% and 30% oxygen were both written as separate figures."}]
        return Store.run(separate, query, **params)

    separate.run = run_separate
    spec = compose_profile(separate, "PSI-102")
    gauge = next(block for block in spec["blocks"] if block["component"] == "GaugeO2")
    assert gauge["data"]["min"] == 21.0
    assert gauge["data"]["max"] == 21.0
    assert "21–30" not in gauge["data"]["note"]
    assert "21.0–30" not in json.dumps(gauge)

    quiet = Store()

    def run_quiet(query, **params):
        if "oxygen" in (query or "").lower():
            return [{"chunkId": "c-none", "text": "The oxygen sensor was installed. No percent is written."}]
        return Store.run(quiet, query, **params)

    quiet.run = run_quiet
    spec = compose_profile(quiet, "PSI-102")
    assert missing("oxygen") in spec["blocks"]
    assert all(block["component"] != "GaugeO2" for block in spec["blocks"])


def test_profile_fuels_from_tested_fuel_when_catalog_empty() -> None:
    store = Store()
    store.cards["PSI-102"]["fuels"] = []

    def run(query, **params):
        if "TESTED_FUEL" in (query or ""):
            return [{"id": "fuel:pmma@PSI-102", "label": "PMMA", "aliases": ["pmma"]}]
        return Store.run(store, query, **params)

    store.run = run
    spec = compose_profile(store, "PSI-102")
    fuels = next(
        block for block in spec["blocks"] if block["component"] == "ChipList" and block["data"]["title"] == "Nhiên liệu / vật liệu"
    )
    assert fuels["data"]["items"] == [{"label": "PMMA"}]
    assert fuels["evidence"][0]["ref"] == "TESTED_FUEL"
    assert fuels["evidence"][0]["id"] == "fuel:pmma@PSI-102"


def test_empty_payload_uses_hardware_studied_and_chunk_count() -> None:
    store = Store()

    def run(query, **params):
        text = query or ""
        psi = params.get("psiId")
        if "USED_HARDWARE" in text:
            if psi == "PSI-102":
                return [{"id": "hardware:dusttrak@PSI-102", "label": "DustTrak"}]
            return []
        if "STUDIED" in text:
            if psi == "PSI-102":
                return [{"id": "phenomenon:smoke@PSI-102", "label": "smoke"}]
            return [{"phenomenonId": "phenomenon:soot", "phenomenonLabel": "soot"}]
        if "documentCount" in text and psi == "PSI-102":
            return [{"psiId": "PSI-102", "documentCount": 1, "chunkCount": 4}]
        return Store.run(store, query, **params)

    store.run = run
    profile = compose_profile(store, "PSI-102")
    hardware = next(
        block
        for block in profile["blocks"]
        if block["component"] == "ChipList" and block["data"]["title"] == "Thiết bị"
    )
    assert hardware["data"]["items"] == [{"label": "DustTrak"}]
    assert hardware["evidence"][0]["ref"] == "USED_HARDWARE"
    assert hardware["evidence"][0]["id"] == "hardware:dusttrak@PSI-102"
    phenomena = next(
        block
        for block in profile["blocks"]
        if block["component"] == "ChipList" and block["data"]["title"] == "Hiện tượng"
    )
    assert phenomena["data"]["items"] == [{"label": "smoke"}]
    assert phenomena["evidence"][0]["ref"] == "STUDIED"
    spec = compose(store, "Compare SAME vs FLEX")
    table = next(block["data"] for block in spec["blocks"] if block["component"] == "CompareTable")
    fields = [row["field"] for row in table["rows"]]
    assert "facility" not in fields
    hardware_row = next(row for row in table["rows"] if row["field"] == "hardware")
    phenomenon_row = next(row for row in table["rows"] if row["field"] == "phenomenon")
    chunks = next(row for row in table["rows"] if row["field"] == "chunkCount")
    assert dict(zip(table["columns"], hardware_row["values"]))["PSI-102"] == "DustTrak"
    assert dict(zip(table["columns"], hardware_row["values"]))["PSI-69"] == "—"
    assert dict(zip(table["columns"], hardware_row["missing"]))["PSI-69"] is True
    assert "USED_HARDWARE" in json.dumps(hardware_row["evidence"])
    assert dict(zip(table["columns"], phenomenon_row["values"]))["PSI-102"] == "smoke"
    assert dict(zip(table["columns"], phenomenon_row["values"]))["PSI-69"] == "—"
    assert "soot" not in json.dumps(phenomenon_row)
    assert dict(zip(table["columns"], chunks["values"]))["PSI-102"] == 4
    assert dict(zip(table["columns"], chunks["missing"]))["PSI-102"] is False
    assert dict(zip(table["columns"], chunks["values"]))["PSI-69"] == "—"
    assert {"kind": "field", "ref": "Chunk"} in chunks["evidence"]


def test_explore_keeps_psi_order_without_scenario_and_counts_families() -> None:
    store = Store()
    store.cards["PSI-102"]["score"] = 1
    store.cards["PSI-69"]["score"] = 99

    def run(query, **params):
        text = query or ""
        if "familyCount" in text:
            return [{"familyCount": 4}]
        if "documentTotal" in text:
            return [{"documentTotal": 11}]
        if "chunkTotal" in text:
            return [{"chunkTotal": 20}]
        return Store.run(store, query, **params)

    store.run = run
    spec = compose(store, "Liệt kê investigation")
    cards = [block for block in spec["blocks"] if block["component"] == "InvestigationCard"]
    assert [card["data"]["id"] for card in cards] == ["PSI-102", "PSI-69"]
    stats = {
        block["data"]["label"]: block["data"]["value"]
        for block in spec["blocks"]
        if block["component"] == "StatCard"
    }
    assert stats["Số family"] == 4
    assert stats["Số tài liệu"] == 11
    assert stats["Số chunk"] == 20
    assert stats["Hồ sơ"] == 2


def test_location_filter_uses_relationship_rows() -> None:
    store = Store()

    def run(query, **params):
        if "Location" in (query or ""):
            assert params["label"] == "iss"
            return [{"psiId": "PSI-102"}]
        return Store.run(store, query, **params)

    store.run = run
    spec = compose(
        store,
        "Liệt kê investigation",
        {"filters": [{"kind": "location", "id": "location:iss@PSI-107", "label": "ISS"}]},
    )
    cards = [block for block in spec["blocks"] if block["component"] == "InvestigationCard"]
    assert [card["data"]["id"] for card in cards] == ["PSI-102"]
    bare = compose(Store(), "Liệt kê investigation", {"filters": [{"kind": "location", "label": "ISS"}]})
    assert [block for block in bare["blocks"] if block["component"] == "InvestigationCard"] == []


def test_graph_depth_comes_from_state_and_weight_from_mention_count() -> None:
    store = Store()
    seen = {}

    def graph_neighbors(psi_id, depth=1):
        seen["depth"] = depth
        return {
            "id": psi_id,
            "name": "SAME",
            "links": [
                {
                    "relation": "USED_HARDWARE",
                    "id": "hardware:dusttrak@PSI-102",
                    "type": "hardware",
                    "label": "DustTrak",
                    "fromId": psi_id,
                    "mentionCount": 4,
                },
                {
                    "relation": "STUDIED",
                    "id": "phenomenon:smoke@PSI-102",
                    "type": "phenomenon",
                    "label": "smoke",
                    "fromId": psi_id,
                },
            ],
        }

    def run(query, **params):
        if "MENTIONS" in (query or ""):
            return [{"id": "phenomenon:smoke@PSI-102", "n": 9}]
        return []

    store.graph_neighbors = graph_neighbors
    store.run = run
    spec = compose(store, "Đồ thị quanh PSI-102", {"depth": 2})
    assert seen["depth"] == 2
    graph = spec["blocks"][0]["data"]
    weights = {node["id"]: node["weight"] for node in graph["nodes"]}
    assert weights["hardware:dusttrak@PSI-102"] == 4
    assert weights["phenomenon:smoke@PSI-102"] == 9
    assert weights["PSI-102"] == 1
    assert all(edge["type"] != "MENTIONS" for edge in graph["edges"])
    assert len(graph["nodes"]) <= 60
    assert len(graph["edges"]) <= 120
    compose(store, "Đồ thị quanh PSI-102", {"depth": 9})
    assert seen["depth"] == 2
    compose(store, "Đồ thị quanh PSI-102", {"depth": 0})
    assert seen["depth"] == 1


def test_coverage_and_export_together_keep_both_views(monkeypatch, tmp_path) -> None:
    from app import viz

    monkeypatch.setattr(viz, "COVERAGE_PATH", tmp_path / "viz_coverage.json")
    question = "Dữ liệu phủ tới đâu? Xuất bảng luôn"
    assert classify(question, [], None) == "coverage"
    assert classify("Xuất bảng dữ liệu", [], None) == "extract"
    spec = compose(Store(), question)
    names = [block["component"] for block in spec["blocks"]]
    assert spec["intent"] == "coverage"
    assert "BarChart" in names
    assert "HeatMap" in names
    assert names.count("DataTable") == 2
    assert "ExportCSV" in names


def test_heatmap_axis_uses_source_share_not_raw_total(monkeypatch, tmp_path) -> None:
    from app import viz

    monkeypatch.setattr(viz, "COVERAGE_PATH", tmp_path / "viz_coverage.json")
    store = Store()
    store.chunk_counts = [
        {"source": "ACME_common", "n": 1000},
        {"source": "small", "n": 10},
    ]
    store.heatmap_rows = [
        {
            "fuelId": "fuel:ethylene",
            "fuelLabel": "Ethylene",
            "phenomenonId": "phenomenon:soot",
            "phenomenonLabel": "soot",
            "source": "ACME_common",
            "n": 100,
        },
        {
            "fuelId": "fuel:c2h4",
            "fuelLabel": "C2H4",
            "phenomenonId": "phenomenon:soot",
            "phenomenonLabel": "soot",
            "source": "small",
            "n": 10,
        },
    ]
    spec = compose(store, "Dữ liệu phủ tới đâu")
    heatmap = next(block for block in spec["blocks"] if block["component"] == "HeatMap")
    assert heatmap["data"]["y"][0] == "C2H4"
    assert "Ethylene" in heatmap["data"]["y"]
    assert heatmap["data"]["y"][0] != "Ethylene"
    by_fuel = {cell["y"]: cell["v"] for cell in heatmap["data"]["cells"]}
    assert by_fuel["C2H4"] == 1.0
    assert by_fuel["Ethylene"] == 0.1
    assert "không theo tổng MENTIONS thô" in heatmap["data"]["caption"]


def test_sensor_aliases_merge_and_unrelated_ids_stay_split() -> None:
    store = Store()
    store.sensor_rows = [
        {
            "sensorId": "sensor:dusttrak@PSI-47",
            "label": "DustTrak",
            "aliases": ["ptrak"],
            "psiId": "PSI-47",
            "n": 2,
        },
        {
            "sensorId": "sensor:ptrak@PSI-102",
            "label": "ptrak",
            "psiId": "PSI-102",
            "n": 3,
        },
        {
            "sensorId": "sensor:ethylene@PSI-25",
            "label": "Ethylene",
            "psiId": "PSI-25",
            "n": 1,
        },
        {
            "sensorId": "sensor:c2h4@PSI-25",
            "label": "C2H4",
            "psiId": "PSI-25",
            "n": 1,
        },
    ]
    spec = compose(store, "Cảm biến nào phát hiện khói tốt")
    matrix = next(block["data"] for block in spec["blocks"] if block["component"] == "SensorMatrix")
    dust = next(sensor for sensor in matrix["sensors"] if "dusttrak" in sensor["id"] or "ptrak" in sensor["id"])
    assert set(dust["rawIds"]) == {"sensor:dusttrak@PSI-47", "sensor:ptrak@PSI-102"}
    labels = {sensor["label"] for sensor in matrix["sensors"]}
    assert "Ethylene" in labels
    assert any("C2H4" in sensor["label"] or sensor["id"] == "sensor:c2h4@PSI-25" for sensor in matrix["sensors"])
    assert sum(1 for sensor in matrix["sensors"] if sensor["id"] in {"sensor:ethylene@PSI-25", "sensor:c2h4@PSI-25"} or "Ethylene" in sensor["label"] or "C2H4" in sensor["label"]) == 2


def test_evidence_passes_pages_span_and_keeps_figures_empty() -> None:
    store = Store()

    def local_search(query, limit):
        return {
            "hits": [
                {
                    "chunkId": "c-span",
                    "preview": "A quoted sentence without a new number.",
                    "page": 3,
                    "pages_span": [3, 4],
                    "source": "report.pdf",
                    "documentTitle": "Report",
                    "figures": ["should-not-copy.png"],
                }
            ]
        }

    store.local_search = local_search
    spec = compose(store, "trang nào nói về oxy?")
    chunk = spec["blocks"][0]["data"]["chunks"][0]
    assert chunk["pages_span"] == [3, 4]
    assert chunk["figures"] == []
    assert spec["blocks"][0]["evidence"][0]["chunkId"] == "c-span"


def test_topic_lists_only_stored_ids() -> None:
    store = Store()
    store.communities = [
        {
            "id": "community:L2:1",
            "title": "Smoke",
            "summary": "Theme",
            "level": 2,
            "size": 2,
            "parent": None,
            "psiIds": ["PSI-102"],
            "chunkIds": ["c-9"],
        }
    ]
    spec = compose(store, "Bản đồ chủ đề")
    community = spec["blocks"][0]["data"]["communities"][0]
    assert community["psiIds"] == ["PSI-102"]
    assert community["chunkIds"] == ["c-9"]
    cards = [block for block in spec["blocks"] if block["component"] == "InvestigationCard"]
    assert [card["data"]["id"] for card in cards] == ["PSI-102"]
    assert "21.5" not in json.dumps(spec)


def test_scenario_recommendation_cites_relevant_to() -> None:
    spec = compose(Store(), "Thí nghiệm nào liên quan nhất cho trạm ISS?")
    assert spec["intent"] == "scenario"
    investigation = spec["blocks"][0]["data"]["investigations"][0]
    assert investigation["evidence"][0]["ref"] == "RELEVANT_TO"
    assert investigation["evidence"][0]["id"] == investigation["id"]
    assert "gaps" not in spec["blocks"][0]["data"]
