"""Build a PSI catalog and knowledge graph from experiment tables.

Facts come from Experiment Table CSVs and ISA-Tab metadata.
Relevance scores come from data/profiles.json.
PDF reports are listed by filename only.
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PSI = ROOT / "data" / "psi"
PROFILES = ROOT / "data" / "profiles.json"
OUT_DIR = ROOT / "backend" / "var"

SCENARIO_NODES = {
    "iss": "ISS cabin (21% O2, 14.7 psi)",
    "moon": "Lunar habitat (≥30% O2, ≤10.2 psi)",
    "suit": "Spacesuit (100% O2, 4.3 psi)",
    "earth": "Earth fire safety",
}

FAMILIES = {
    "PSI-10": ("cir-acme", "CIR + ACME, gaseous flames"),
    "PSI-20": ("cir-acme", "CIR + ACME, gaseous flames"),
    "PSI-21": ("cir-acme", "CIR + ACME, gaseous flames"),
    "PSI-22": ("cir-acme", "CIR + ACME, gaseous flames"),
    "PSI-23": ("cir-acme", "CIR + ACME, gaseous flames"),
    "PSI-159": ("cir-acme", "CIR + ACME, gaseous flames"),
    "PSI-39": ("cir-mdca", "CIR + MDCA, liquid droplets"),
    "PSI-68": ("cir-mdca", "CIR + MDCA, liquid droplets"),
    "PSI-69": ("cir-mdca", "CIR + MDCA, liquid droplets"),
    "PSI-25": ("msg-duct", "MSG glovebox, SPICE fan duct"),
    "PSI-26": ("msg-duct", "MSG glovebox, SPICE fan duct"),
    "PSI-106": ("msg-duct", "MSG glovebox, SPICE fan duct"),
    "PSI-107": ("msg-duct", "MSG glovebox, SPICE fan duct"),
    "PSI-98": ("cygnus", "Cygnus vehicle after it left the ISS"),
    "PSI-99": ("cygnus", "Cygnus vehicle after it left the ISS"),
    "PSI-100": ("cygnus", "Cygnus vehicle after it left the ISS"),
    "PSI-47": ("aerosol", "ISS aerosol instrument (SAME and DAFT)"),
    "PSI-101": ("aerosol", "ISS aerosol instrument (SAME and DAFT)"),
    "PSI-102": ("aerosol", "ISS aerosol instrument (SAME and DAFT)"),
    "PSI-60": ("analysis", "Analysis or model, not a new burn"),
    "PSI-62": ("analysis", "Analysis or model, not a new burn"),
    "PSI-115": ("analysis", "Analysis or model, not a new burn"),
    "PSI-117": ("analysis", "Analysis or model, not a new burn"),
    "PSI-142": ("analysis", "Analysis or model, not a new burn"),
    "PSI-108": ("stdce", "USML-1 Spacelab, not a fire test"),
}

CONTEXT = [
    {
        "id": "ctx:apollo1",
        "label": "Apollo 1",
        "summary": "In 1967 a fire in a pure-oxygen cabin during a pre-launch test killed the Apollo 1 crew.",
        "url": "https://sma.nasa.gov/sma-disciplines/accllp/apollo-1",
        "scenarios": ["suit", "moon"],
    },
    {
        "id": "ctx:apollo13",
        "label": "Apollo 13",
        "summary": "An oxygen-tank explosion on the way to the Moon disabled the service module.",
        "url": "https://sma.nasa.gov/sma-disciplines/accllp/apollo-13",
        "scenarios": ["iss", "moon"],
    },
    {
        "id": "ctx:mir",
        "label": "Mir oxygen-generator fire",
        "summary": "In 1997 an oxygen-generator failure started a fire aboard Mir during joint operations.",
        "url": "https://www.nasa.gov/history/25-years-ago-fire-aboard-space-station-mir/",
        "scenarios": ["iss", "moon"],
    },
    {
        "id": "ctx:air-tanks",
        "label": "Firefighter air tanks",
        "summary": "1970s spacesuit breathing technology was adapted for firefighter air tanks.",
        "url": "https://spinoff.nasa.gov/node/9582",
        "scenarios": ["earth", "suit"],
    },
    {
        "id": "ctx:detector",
        "label": "Shuttle smoke detector",
        "summary": "A shuttle detector that separates smoke from dust, later used where dust causes nuisance alarms, including mines.",
        "url": "https://spinoff.nasa.gov/node/9407",
        "scenarios": ["earth", "iss"],
    },
    {
        "id": "ctx:attire",
        "label": "Firefighter clothing",
        "summary": "1980s spacesuit materials were applied to firefighter protective clothing.",
        "url": "https://spinoff.nasa.gov/node/9918",
        "scenarios": ["earth"],
    },
]

FUEL_HINTS = (
    "fuel sample material",
    "sample material",
    "material",
    "fuel bottle",
    "fuel type",
    "sample (fuel type)",
    "fuel",
)
FLOW_HINTS = ("air flow", "flow velocity", "air rate")
SIZE_HINTS = ("thickness", "length", "width", "diameter", "burner size", "nozzle")
MEASUREMENT_HINTS = (
    "camera",
    "radiometer",
    "thermocouple",
    "spectrometer",
    "photodiode",
    "temperature",
    "temp",
    "burn time",
    "burning rate",
    "weight",
    "extinction",
    "power",
    "pressure",
    "tad",
    "soot",
    "radiance",
    "velocity",
    "diameter",
)
ID_COLUMN = re.compile(
    r"date|gmt|\bday\b|\byear\b|\bmonth\b|\btime\b|\btest\b|\brun\b|sample\s*#|sample number|"
    r"identifier|\bnotes\b|\bcomment|\bframes\b|^pi$",
    re.I,
)
OXYGEN_SKIP = ("flow", "co2", "co initial", "nitrogen", "helium", "ppm", "final", "after", "mg/s")
NUMBER = re.compile(r"\d+(?:\.\d+)?")


def collapse(text: str) -> str:
    text = text.replace("\r", " ").replace("\n", " ")
    text = re.sub(r"\s+", " ", text).strip().strip('"')
    return text


def metadata_field(text: str, name: str) -> str:
    match = re.search(rf"(?:^|\n){re.escape(name)}\t", text)
    if not match:
        return ""
    rest = text[match.end():]
    if rest.startswith('"'):
        chars: list[str] = []
        index = 1
        while index < len(rest):
            if rest[index] == '"':
                if index + 1 < len(rest) and rest[index + 1] == '"':
                    chars.append('"')
                    index += 2
                    continue
                break
            chars.append(rest[index])
            index += 1
        return collapse("".join(chars))
    return collapse(rest.splitlines()[0].split("\t")[0])


def clip(text: str, limit: int) -> str:
    text = collapse(text)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0] + "…"


def slug(text: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return text[:48] or "unknown"


def find_dir(psi_id: str) -> Path | None:
    number = psi_id.removeprefix("PSI-")
    matches = [path for path in PSI.iterdir() if path.is_dir() and path.name.startswith(f"PSI-{number}_")]
    return matches[0] if matches else None


def read_metadata(directory: Path | None) -> dict:
    if directory is None:
        return {}
    folder = directory / "metadata"
    if not folder.is_dir():
        return {}
    investigations = sorted(folder.glob("i_Investigation.txt"))
    if not investigations:
        return {}
    text = investigations[0].read_text(encoding="utf-8", errors="replace")
    return {
        "title": metadata_field(text, "Study Title"),
        "objective": metadata_field(text, "Comment[Objective]"),
        "hardware": metadata_field(text, "Comment[Experiment Hardware]"),
        "acronym": metadata_field(text, "Comment[Investigation Acronym]"),
        "doi": metadata_field(text, "Comment[DOI]"),
        "source": str(investigations[0].relative_to(ROOT)),
    }


def report_pdfs(directory: Path | None) -> list[str]:
    if directory is None:
        return []
    found: list[str] = []
    for folder_name in ("Reports", "Science Documents"):
        folder = directory / folder_name
        if not folder.is_dir():
            continue
        for path in sorted(folder.rglob("*.pdf")):
            found.append(str(path.relative_to(ROOT)))
    return found


def experiment_tables(directory: Path | None) -> list[Path]:
    if directory is None:
        return []
    folder = directory / "Experiment Table"
    if not folder.is_dir():
        return []
    tables = []
    for path in sorted(folder.glob("*.csv")):
        lowered = path.name.lower()
        if "(1)" in path.name or "alternate" in lowered:
            continue
        tables.append(path)
    return tables


def read_table(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8", errors="replace", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = [name.lstrip("\ufeff").strip() for name in (reader.fieldnames or [])]
        rows = []
        for raw in reader:
            row = {}
            empty = True
            for key, value in raw.items():
                if key is None:
                    continue
                name = key.lstrip("\ufeff").strip()
                cell = collapse(value or "")
                row[name] = cell
                if cell:
                    empty = False
            if not empty:
                rows.append(row)
    return fieldnames, rows


def norm(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip().lower()


def is_oxygen_column(name: str) -> bool:
    text = norm(name)
    if any(token in text for token in OXYGEN_SKIP):
        return False
    if "o2" in text or "oxygen" in text or "xo2" in text:
        return True
    return bool(re.search(r"(^|[^a-z])o initial", text))


def is_fuel_column(name: str) -> bool:
    text = norm(name)
    if "rate" in text or "conc" in text or "range" in text:
        return False
    return any(hint in text for hint in FUEL_HINTS)


def is_flow_column(name: str) -> bool:
    text = norm(name)
    return any(hint in text for hint in FLOW_HINTS)


def is_size_column(name: str) -> bool:
    text = norm(name)
    if any(token in text for token in ("extinction", "flame", "burn length", "spread", "burn time")):
        return False
    return any(hint in text for hint in SIZE_HINTS)


def is_id_column(name: str) -> bool:
    return bool(ID_COLUMN.search(norm(name)))


def numbers_in(cell: str) -> list[float]:
    return [float(item) for item in NUMBER.findall(cell.replace(",", ""))]


def as_percent(name: str, values: list[float]) -> list[float]:
    text = norm(name)
    already_percent = "%" in name or "percent" in text
    kept: list[float] = []
    for value in values:
        if already_percent:
            percent = value
        elif value > 1:
            continue
        else:
            percent = value * 100
        if 1 <= percent <= 100:
            kept.append(round(percent, 1))
    return kept


STABLE_FORMULAS = {
    "CH4",
    "C2H2",
    "C2H4",
    "C2H6",
    "C3H6",
    "C3H8",
    "C4H10",
    "C7H16",
    "C8H18",
    "C12H26",
}


def plausible_fuel(text: str) -> bool:
    if re.search(r"\bN3\b", text.upper()):
        return False
    formulas = re.findall(r"C\d*H\d+", text.upper())
    if not formulas:
        return True
    return all(formula in STABLE_FORMULAS for formula in formulas)


def fuel_values(cell: str) -> list[str]:
    text = collapse(cell)
    if not text:
        return []
    if "same conditions" in text.lower():
        text = collapse(text.split(":")[0])
    if "visul" in text.lower() or "n/a" in text.lower() or text.lower() in {"na", "none", "null"}:
        return []
    if not re.search(r"[A-Za-z]", text) or len(text) > 120 or not plausible_fuel(text):
        return []
    return [text]


def unique_keep(values: list[str], limit: int) -> list[str]:
    seen: list[str] = []
    for value in values:
        if value not in seen:
            seen.append(value)
        if len(seen) == limit:
            break
    return seen


def unit_of(name: str) -> str:
    match = re.search(r"\(([^)]+)\)", name)
    return match.group(1).strip() if match else ""


def sample_form(columns: list[str], fuels: list[str]) -> str | None:
    text = " ".join(norm(name) for name in columns)
    if "droplet" in text:
        return "droplet"
    if "burner size" in text:
        return "circular porous burner"
    if "nozzle" in text:
        return "nozzle in a coflow duct"
    if "thickness" in text and ("length" in text or "width" in text):
        return "flat solid sample"
    fuel_text = " ".join(fuels).lower()
    if any(token in fuel_text for token in ("film", "fabric", "pmma", "sibal", "silicone", "nomex", "teflon", "kapton")):
        return "flat solid sample"
    if "dust" in fuel_text:
        return "dust aerosol"
    return None


def column_facts(name: str, rows: list[dict[str, str]]) -> dict | None:
    if is_id_column(name):
        return None
    values = [row.get(name, "") for row in rows if row.get(name, "")]
    if len(values) < 2:
        return None
    distinct = unique_keep(values, 8)
    distinct_count = len({value for value in values})
    if distinct_count <= 1:
        shown = distinct[0]
        return {"kind": "constant", "name": name, "value": shown if len(shown) <= 80 else shown[:77] + "…"}
    return {"kind": "varied", "name": name, "distinct": distinct_count}


def stats_from_tables(paths: list[Path]) -> dict:
    fuels: list[str] = []
    oxygen: list[float] = []
    flows: list[float] = []
    runs = 0
    sources: list[str] = []
    columns: list[str] = []
    measurements: list[str] = []
    sizes: list[dict] = []
    varied: list[dict] = []
    constant: list[dict] = []
    varied_names: set[str] = set()
    constant_by_name: dict[str, str] = {}

    for path in paths:
        fieldnames, rows = read_table(path)
        sources.append(str(path.relative_to(ROOT)))
        runs += len(rows)
        for name in fieldnames:
            if name not in columns:
                columns.append(name)
            text = norm(name)
            if any(hint in text for hint in MEASUREMENT_HINTS) and name not in measurements:
                measurements.append(name)
            cells = [row.get(name, "") for row in rows]
            if is_oxygen_column(name):
                for cell in cells:
                    oxygen.extend(as_percent(name, numbers_in(cell)))
            if is_fuel_column(name):
                for cell in cells:
                    fuels.extend(fuel_values(cell))
            if is_flow_column(name):
                for cell in cells:
                    flows.extend(numbers_in(cell))
            if is_size_column(name):
                nums = []
                for cell in cells:
                    nums.extend(numbers_in(cell))
                if nums:
                    sizes.append({
                        "column": name,
                        "min": min(nums),
                        "max": max(nums),
                        "unit": unit_of(name),
                    })
            fact = column_facts(name, rows)
            if fact is None:
                continue
            if fact["kind"] == "varied":
                varied_names.add(name)
                constant_by_name.pop(name, None)
                if not any(item["name"] == name for item in varied):
                    varied.append({"name": name, "distinct": fact["distinct"]})
            elif name not in varied_names and name not in constant_by_name:
                constant_by_name[name] = fact["value"]

    varied.sort(key=lambda item: item["distinct"], reverse=True)
    kept_fuels = unique_keep(fuels, 12)
    flow = None
    if flows:
        flow = f"{min(flows):g}–{max(flows):g}" if min(flows) != max(flows) else f"{min(flows):g}"
    return {
        "fuels": kept_fuels,
        "sampleForm": sample_form(columns, kept_fuels),
        "oxygenMin": round(min(oxygen), 1) if oxygen else None,
        "oxygenMax": round(max(oxygen), 1) if oxygen else None,
        "flow": flow,
        "runCount": runs or None,
        "sampleSize": sizes[:8],
        "measurements": measurements[:16],
        "varied": varied[:16],
        "constant": [{"name": name, "value": value} for name, value in list(constant_by_name.items())[:12]],
        "sources": sources,
    }


def oxygen_note(psi_id: str, oxygen_max: float | None) -> str | None:
    if psi_id == "PSI-10" and (oxygen_max or 0) >= 80:
        return (
            "The inverse-flame table reaches 85% oxygen. The challenge names CFI-G as the "
            "high-oxygen microgravity series. Flame Design is flame structure, not the habitat or suit screen."
        )
    if psi_id == "PSI-159":
        return (
            "CFI-G flows an oxygen-nitrogen mixture from a spherical burner into a fuel-nitrogen "
            "chamber, an inside-out flame, up to about 85% oxygen."
        )
    return None


def add_edge(edges: list[dict], source: str, target: str, kind: str, **extra) -> None:
    edge = {"source": source, "target": target, "type": kind, **extra}
    if edge not in edges:
        edges.append(edge)


def build() -> tuple[dict, dict]:
    profiles = json.loads(PROFILES.read_text())
    investigations = []
    nodes: list[dict] = []
    edges: list[dict] = []
    node_ids: set[str] = set()

    def add_node(node: dict) -> None:
        if node["id"] in node_ids:
            return
        node_ids.add(node["id"])
        nodes.append(node)

    for scenario, label in SCENARIO_NODES.items():
        add_node({"id": f"scenario:{scenario}", "type": "scenario", "label": label})
    add_node({
        "id": "scenario:mars",
        "type": "scenario",
        "label": "Mars surface habitat (partial gravity, no PSI combustion data yet)",
        "caveat": "No partial-gravity combustion measurements are in PSI. Ranking uses the lunar enriched-oxygen band as an atmosphere bound. E-FIELD Flames mimics a body force. Microgravity and normal gravity bound Martian gravity.",
    })
    add_node({
        "id": "scenario:rover",
        "type": "scenario",
        "label": "Pressurized rover (≥30% O2, ≤10.2 psi)",
        "caveat": "A pressurized rover is expected in the same oxygen and pressure band as the lunar habitat.",
    })
    add_edge(edges, "scenario:mars", "scenario:moon", "USES_ATMOSPHERE_BOUND")
    add_edge(edges, "scenario:rover", "scenario:moon", "USES_ATMOSPHERE_BOUND")
    for role, label in (
        ("prevention", "Prevention"),
        ("detection", "Detection"),
        ("suppression", "Suppression"),
    ):
        add_node({"id": f"role:{role}", "type": "role", "label": label})
    for item in CONTEXT:
        add_node({"id": item["id"], "type": "context", "label": item["label"], "summary": item["summary"], "url": item["url"]})
        for scenario in item["scenarios"]:
            add_edge(edges, item["id"], f"scenario:{scenario}", "INFORMS")

    for psi_id, profile in profiles.items():
        directory = find_dir(psi_id)
        meta = read_metadata(directory)
        measured = stats_from_tables(experiment_tables(directory))
        family_id, family_label = FAMILIES[psi_id]
        summary = meta.get("objective") or profile["blurb"]
        summary = clip(summary, 700)
        hardware = meta.get("hardware") or profile["hardware"]
        record = {
            "id": psi_id,
            "name": profile["name"],
            "acronym": meta.get("acronym") or profile["name"],
            "role": profile["role"],
            "roles": profile["roles"],
            "hardware": hardware,
            "familyId": family_id,
            "family": family_label,
            "summary": summary,
            "blurb": profile["blurb"],
            "fuels": measured["fuels"],
            "sampleForm": measured["sampleForm"],
            "sampleSize": measured["sampleSize"],
            "oxygenMin": measured["oxygenMin"],
            "oxygenMax": measured["oxygenMax"],
            "oxygenNote": oxygen_note(psi_id, measured["oxygenMax"]),
            "flow": measured["flow"],
            "runCount": measured["runCount"],
            "measurements": measured["measurements"],
            "varied": measured["varied"],
            "constant": measured["constant"],
            "relevance": profile["relevance"],
            "scores": profile["scores"],
            "doi": meta.get("doi") or "",
            "reports": report_pdfs(directory),
            "sources": measured["sources"] + ([meta["source"]] if meta.get("source") else []),
        }
        investigations.append(record)

        inv = f"inv:{psi_id}"
        add_node({
            "id": inv,
            "type": "investigation",
            "label": profile["name"],
            "psiId": psi_id,
            "summary": summary,
            "oxygenMin": measured["oxygenMin"],
            "oxygenMax": measured["oxygenMax"],
            "flow": measured["flow"],
            "runCount": measured["runCount"],
            "family": family_label,
        })
        family_node = f"family:{family_id}"
        add_node({"id": family_node, "type": "family", "label": family_label})
        add_edge(edges, inv, family_node, "IN_FAMILY")
        hw = f"hw:{slug(hardware)}"
        add_node({"id": hw, "type": "hardware", "label": hardware})
        add_edge(edges, inv, hw, "USED_HARDWARE")
        for role in profile["roles"]:
            add_edge(edges, inv, f"role:{role}", "HAS_ROLE")
        for fuel in measured["fuels"]:
            fuel_id = f"fuel:{slug(fuel)}"
            add_node({"id": fuel_id, "type": "fuel", "label": fuel})
            add_edge(edges, inv, fuel_id, "TESTED_FUEL")
        for scenario, score in profile["scores"].items():
            add_edge(
                edges,
                inv,
                f"scenario:{scenario}",
                "RELEVANT_TO",
                score=score,
                reason=profile["relevance"][scenario],
            )

    catalog = {
        "scenarios": [{"id": key, "label": label} for key, label in SCENARIO_NODES.items()]
        + [
            {"id": "mars", "label": "Mars surface habitat (partial gravity, no PSI combustion data yet)"},
            {"id": "rover", "label": "Pressurized rover (≥30% O2, ≤10.2 psi)"},
        ],
        "investigations": investigations,
        "context": CONTEXT,
    }
    graph = {"nodes": nodes, "edges": edges}
    return catalog, graph


def check(catalog: dict) -> None:
    by_id = {item["id"]: item for item in catalog["investigations"]}
    assert len(by_id) == 25, len(by_id)
    cfi = by_id["PSI-159"]
    assert cfi["oxygenMin"] is not None and cfi["oxygenMin"] <= 15, cfi["oxygenMin"]
    assert cfi["oxygenMax"] is not None and cfi["oxygenMax"] >= 80, cfi["oxygenMax"]
    bre = by_id["PSI-20"]
    assert bre["oxygenMin"] == 21.0 and bre["oxygenMax"] == 40.0, (bre["oxygenMin"], bre["oxygenMax"])
    bass = by_id["PSI-25"]
    assert bass["oxygenMin"] is not None and bass["oxygenMin"] >= 13, bass["oxygenMin"]
    assert bass["oxygenMax"] is not None and bass["oxygenMax"] <= 25, bass["oxygenMax"]
    flex = by_id["PSI-69"]
    assert flex["oxygenMin"] is not None and flex["oxygenMin"] <= 15, flex["oxygenMin"]
    assert flex["oxygenMax"] is not None and flex["oxygenMax"] >= 30, flex["oxygenMax"]
    same = by_id["PSI-102"]
    assert "Teflon" in same["fuels"], same["fuels"]
    assert all("same conditions" not in fuel.lower() for fuel in same["fuels"])
    assert by_id["PSI-10"]["oxygenNote"]
    assert by_id["PSI-98"]["sampleForm"] == "flat solid sample"
    assert by_id["PSI-25"]["sampleForm"] == "flat solid sample"
    assert by_id["PSI-69"]["sampleForm"] == "droplet"
    assert "40% CH5" not in by_id["PSI-21"]["fuels"]
    assert any("CH4" in fuel for fuel in by_id["PSI-21"]["fuels"])
    assert any("dust" in fuel.lower() for fuel in by_id["PSI-47"]["fuels"])


def main() -> None:
    catalog, graph = build()
    check(catalog)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "catalog.json").write_text(json.dumps(catalog, indent=2) + "\n")
    (OUT_DIR / "graph.json").write_text(json.dumps(graph, indent=2) + "\n")
    print(f"investigations {len(catalog['investigations'])}")
    print(f"nodes {len(graph['nodes'])} edges {len(graph['edges'])}")
    for item in catalog["investigations"]:
        oxygen = (
            f"{item['oxygenMin']}–{item['oxygenMax']}%"
            if item["oxygenMin"] is not None
            else "no O2 column"
        )
        flow = item["flow"] or "no flow"
        print(
            f"{item['id']:8} {oxygen:16} flow {flow:12} runs {str(item['runCount']):5} "
            f"fuels {len(item['fuels']):2} varied {len(item['varied']):2} {item['family']}"
        )
    print(f"wrote {OUT_DIR / 'catalog.json'}")


if __name__ == "__main__":
    main()
