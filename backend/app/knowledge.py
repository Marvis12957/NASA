"""Scenario detection, aliases, and catalog-backed display helpers."""

from __future__ import annotations

import re

from app.catalog import FAMILIES, SCENARIO_NODES

SCENARIO_IDS = ("iss", "moon", "mars", "rover", "suit", "earth")
SCORE_KEYS = ("iss", "moon", "suit", "earth")
ROLE_IDS = ("prevention", "detection", "suppression")

# Labels from catalog. Mars/rover caveats are the Scenario node notes in catalog.py.
# Other caveats are the fire-safety bounds used when ranking those environments.
SCENARIOS: dict[str, dict] = {
    "iss": {
        "id": "iss",
        "label": SCENARIO_NODES["iss"],
        "score_key": "iss",
        "caveat": (
            "Station air is the Earth mix. Artemis II showed how fast a smoke alarm "
            "gets a crew's attention: the commander said it does, even 80,000 miles from home."
        ),
    },
    "moon": {
        "id": "moon",
        "label": SCENARIO_NODES["moon"],
        "score_key": "moon",
        "caveat": (
            "PSI has no partial-gravity combustion data. E-FIELD Flames is the stand-in: "
            "an electric field pulls on ions in the flame and mimics a body force. "
            "Microgravity and normal gravity bound lunar gravity. LUCI and FM2 are the "
            "flight experiments expected to add real partial-g burns."
        ),
    },
    "mars": {
        "id": "mars",
        "label": "Mars surface habitat (partial gravity, no PSI combustion data yet)",
        "score_key": "moon",
        "caveat": (
            "No partial-gravity combustion measurements are in PSI. Ranking uses the lunar "
            "enriched-oxygen band as an atmosphere bound. E-FIELD Flames mimics a body force. "
            "Microgravity and normal gravity bound Martian gravity."
        ),
    },
    "rover": {
        "id": "rover",
        "label": "Pressurized rover (≥30% O2, ≤10.2 psi)",
        "score_key": "moon",
        "caveat": (
            "A pressurized rover is expected in the same oxygen and pressure band as the lunar habitat."
        ),
    },
    "suit": {
        "id": "suit",
        "label": SCENARIO_NODES["suit"],
        "score_key": "suit",
        "caveat": (
            "A suit is pure oxygen at 4.3 psi so the joints can flex. Crews pre-breathe oxygen "
            "to flush nitrogen and avoid the bends. CFI-G is the high-oxygen microgravity series, "
            "up to about 85% oxygen, but it does not reproduce 4.3 psi. Apollo 1 is the historical "
            "pure-oxygen cabin fire."
        ),
    },
    "earth": {
        "id": "earth",
        "label": SCENARIO_NODES["earth"],
        "score_key": "earth",
        "caveat": (
            "Fine-water-mist extinguishers developed for the ISS are used on the ground. The same "
            "smoke work led to detectors that separate smoke from dust, and suit materials led to "
            "firefighter clothing and air tanks."
        ),
    },
}

SCENARIO_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("mars", re.compile(r"\bmars\b|martian|sao hỏa|sao hoa", re.I)),
    ("rover", re.compile(r"\brover", re.I)),
    ("suit", re.compile(r"\bsuit\b|spacesuit|bộ đồ|bo do|\beva\b|4\.3\s*psi", re.I)),
    ("earth", re.compile(r"\bearth\b|terrestrial|firefighter|trái đất|trai dat|spin-?off", re.I)),
    ("moon", re.compile(r"\bmoon\b|lunar|mặt trăng|mat trang|habitat", re.I)),
    ("iss", re.compile(r"\biss\b|\bstation\b|trạm|cabin|spacecraft", re.I)),
]

ALIASES: dict[str, list[str]] = {
    "PSI-10": ["flame design"],
    "PSI-20": ["burning rate emulator", "bre"],
    "PSI-21": ["coflow laminar", "cld flame", "cld"],
    "PSI-22": ["e-field", "efield"],
    "PSI-23": ["s-flame", "s flame"],
    "PSI-25": ["bass-ii", "bass ii", "bass-2"],
    "PSI-26": ["bass"],
    "PSI-39": ["cool flames investigation"],
    "PSI-47": ["daft"],
    "PSI-60": ["spice data analysis"],
    "PSI-62": ["flamelet"],
    "PSI-68": ["flex-2", "flex 2"],
    "PSI-69": ["flex"],
    "PSI-98": ["saffire-i", "saffire i", "saffire-1"],
    "PSI-99": ["saffire-ii", "saffire ii", "saffire-2"],
    "PSI-100": ["saffire-iii", "saffire iii", "saffire-3"],
    "PSI-101": ["same-r", "same r"],
    "PSI-102": ["smoke aerosol measurement"],
    "PSI-106": ["slice"],
    "PSI-107": ["spice"],
    "PSI-108": ["stdce"],
    "PSI-115": ["smoke modeling", "nnx17af08a"],
    "PSI-117": ["farouk"],
    "PSI-142": ["counterflow"],
    "PSI-159": ["cfi-g", "cfi g"],
}


_GRAVITY_CONTRAST = re.compile(
    r"gravity|trọng lực|trong luc|buoyan|lực nổi|luc noi|weightless|không trọng lực|zero-?g\b|\b[01]\s?g\b",
    re.I,
)
_EARTH_APPLICATION = re.compile(r"spin-?off|firefighter|lính cứu hỏa|ứng dụng|ung dung|application|benefit|giúp ích", re.I)
_MOON_WORDS = re.compile(r"\bmoon\b|lunar|mặt trăng|mat trang|gateway", re.I)
_ISS_WORDS = re.compile(r"\biss\b|\bstation\b|trạm", re.I)


def detect_scenario(question: str) -> str | None:
    for scenario_id, pattern in SCENARIO_PATTERNS:
        if not pattern.search(question):
            continue
        # "Flames on Earth vs in microgravity" contrasts gravity levels; it is not the
        # Earth fire-safety (spin-off) scenario.
        if scenario_id == "earth" and _GRAVITY_CONTRAST.search(question) and not _EARTH_APPLICATION.search(question):
            continue
        # "ISS habitat module": the word habitat alone does not mean the Moon.
        if scenario_id == "moon" and not _MOON_WORDS.search(question) and _ISS_WORDS.search(question):
            continue
        return scenario_id
    return None


def detect_role(question: str) -> str | None:
    q = question.lower()
    if re.search(r"\bdetect|smoke alarm|aerosol|phát hiện|phat hien", q):
        return "detection"
    if re.search(r"\bsuppress|extinguish|dập|dap lua|helium|carbon dioxide|water mist", q):
        return "suppression"
    if re.search(r"\bflammab|material selection|prevention|ngăn cháy|ngan chay|\bfabric\b", q):
        return "prevention"
    return None


def family_of(psi_id: str) -> tuple[str, str]:
    return FAMILIES.get(psi_id, ("other", "Other combustion investigation"))


def format_oxygen(oxygen_min: float | None, oxygen_max: float | None) -> str:
    if oxygen_min is None or oxygen_max is None:
        return "no oxygen column in the tables held here"
    if oxygen_min == oxygen_max:
        return f"{oxygen_min}% oxygen"
    return f"{oxygen_min}–{oxygen_max}% oxygen"


_FLOW_UNIT = re.compile(r"(?i)(?:cm|mm|m|mg)\s*/\s*s\b")
_FLOW_BARE = re.compile(r"^\d+(?:\.\d+)?(?:\s*[–—-]\s*\d+(?:\.\d+)?)?$")


def format_flow(flow: str | None) -> str:
    """Append cm/s only for a bare catalog speed. A value that already has a unit stays as stored."""
    if flow is None:
        return "no airflow column in the tables held here"
    text = str(flow).strip()
    if not text:
        return "no airflow column in the tables held here"
    if _FLOW_UNIT.search(text):
        return text
    if _FLOW_BARE.fullmatch(text):
        return f"{text} cm/s"
    return text


def oxygen_note(record: dict) -> str | None:
    """Use the note stored on the node. Do not fill one from the catalog helper."""
    note = record.get("oxygenNote")
    if note:
        return str(note)
    return None


def _bounded(question: str, alias: str) -> bool:
    escaped = re.escape(alias)
    return re.search(rf"(?<![a-z0-9-]){escaped}(?![a-z0-9-])", question, re.I) is not None


def _alias_entries() -> list[tuple[str, list[str]]]:
    return sorted(
        ALIASES.items(),
        key=lambda item: max(len(alias) for alias in item[1]),
        reverse=True,
    )


def resolve_psi_id(query: str, names: dict[str, str] | None = None) -> str | None:
    direct = re.search(r"PSI[-\s]?(\d+)", query.strip(), re.I)
    if direct:
        return f"PSI-{int(direct.group(1))}"
    q = query.lower()
    for psi_id, aliases in _alias_entries():
        if any(_bounded(q, alias) for alias in aliases):
            return psi_id
    if names:
        for psi_id, name in names.items():
            if name and _bounded(q, name.lower()):
                return psi_id
    return None


_PSI_TOKEN = re.compile(r"\bpsi[-\s]?(\d+)\b", re.I)
_NUMBER_BEFORE = re.compile(r"\d[\d.,]*\s*$")


def _psi_tokens(question: str) -> list[tuple[int, int, str]]:
    """PSI ids written in the question. In "4.3 psi 100% O2" the psi is a pressure unit."""
    found = []
    for match in _PSI_TOKEN.finditer(question):
        if "-" not in match.group(0) and _NUMBER_BEFORE.search(question[: match.start()]):
            continue
        found.append((match.start(), match.end(), f"PSI-{int(match.group(1))}"))
    return found


def mentioned_ids(question: str, names: dict[str, str] | None = None) -> list[str]:
    """Investigations named in the question, in reading order.

    Longer names claim their words first, so "BASS II" is only PSI-25 and "FLEX 2" only
    PSI-68 (before, the shorter alias "bass" / "flex" added PSI-26 / PSI-69 as well).
    """
    hits: list[tuple[int, str]] = []
    seen: set[str] = set()
    taken: list[tuple[int, int]] = []

    def free(start: int, end: int) -> bool:
        return all(end <= left or start >= right for left, right in taken)

    def add(start: int, end: int, psi_id: str) -> None:
        taken.append((start, end))
        if psi_id not in seen:
            seen.add(psi_id)
            hits.append((start, psi_id))

    for start, end, psi_id in _psi_tokens(question):
        add(start, end, psi_id)
    candidates = [(alias, psi_id) for psi_id, aliases in ALIASES.items() for alias in aliases]
    if names:
        candidates += [(name, psi_id) for psi_id, name in names.items() if name]
    candidates.sort(key=lambda item: -len(item[0]))
    for surface, psi_id in candidates:
        for match in re.finditer(rf"(?<![a-z0-9-]){re.escape(surface)}(?![a-z0-9-])", question, re.I):
            if free(match.start(), match.end()):
                add(match.start(), match.end(), psi_id)
                break
    hits.sort(key=lambda item: item[0])
    return [psi_id for _pos, psi_id in hits]
