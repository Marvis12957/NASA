"""Concept schema identity, type mapping, and relation constraints."""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.graphrag import normalize_type, resolve_entity  # noqa: E402
from app.ontology import (  # noqa: E402
    EXTRACT_SYSTEM,
    is_forbidden_entity,
    map_extract_type,
    map_relation,
    schema_version,
    scoped_id,
)

sys.path.insert(0, str(BACKEND / "scripts"))
from extract_graphrag import ensure_scoped_id  # noqa: E402


def test_schema_is_versioned() -> None:
    assert schema_version() == "1.0.0"
    assert "ontology 1.0.0" in EXTRACT_SYSTEM
    assert "fuel:pmma@PSI-25" in EXTRACT_SYSTEM


def test_unknown_types_map_or_reject() -> None:
    assert map_extract_type("Diagnostic", "CIR") == "Sensor"
    assert map_extract_type("Instrument", "color camera") == "Sensor"
    assert map_extract_type("Experiment", "FLEX") == "Investigation"
    assert map_extract_type("Diluent", "Helium") == "Suppressant"
    assert map_extract_type("Material/Fuel", "carbon dioxide") == "Suppressant"
    assert map_extract_type("Person", "Nayagam") is None
    assert map_extract_type("Condition", "21% O2") is None
    assert map_extract_type("MadeUpType", "widget") is None
    assert normalize_type("software", "MDCA") == "Hardware"


def test_relation_aliases_stay_in_ontology() -> None:
    assert map_relation("STUDIES") == "STUDIED"
    assert map_relation("measured by") == "MEASURED_WITH"
    assert map_relation("OCCURS_IN") == "USED_IN"
    assert map_relation("FRIEND_OF") is None


def test_scoped_ids_keep_psi_and_type() -> None:
    assert scoped_id("fuel", "pmma", "PSI-25") == "fuel:pmma@PSI-25"
    assert scoped_id("fuel", "pmma", "PSI-99") == "fuel:pmma@PSI-99"
    assert scoped_id("sensor", "cir", "PSI-69") == "sensor:cir@PSI-69"
    assert scoped_id("hardware", "cir", "PSI-69") == "hardware:cir@PSI-69"
    assert scoped_id("inv", "PSI-69", "PSI-25") == "inv:PSI-69"
    repaired = ensure_scoped_id({"id": "fuel:pmma", "type": "fuel", "label": "PMMA"}, "PSI-25")
    assert repaired["id"] == "fuel:pmma@PSI-25"
    assert "@" in repaired["id"]


def test_resolve_uses_ontology_and_keeps_metadata() -> None:
    mapping = {
        "pmma": "fuel:pmma",
        "flex": "inv:PSI-69",
        "cir": "hw:combustion-integrated-rack-cir-acme",
        "prevention": "role:prevention",
    }
    known = set(mapping.values()) | {"inv:PSI-25", "inv:PSI-69", "inv:PSI-99"}
    pmma = resolve_entity("PMMA", "Material/Fuel", mapping, known, psi="PSI-25")
    assert pmma is not None
    assert pmma["id"] == "fuel:pmma@PSI-25"
    assert pmma["canonical"] == "fuel:pmma"
    assert pmma["aliases"] == ["PMMA"]
    assert pmma["psi"] == "PSI-25"
    helium = resolve_entity("Helium", "Fuel", mapping, known, psi="PSI-10")
    assert helium is not None
    assert helium["id"] == "suppressant:helium@PSI-10"
    radiative = resolve_entity("radiative extinction", "Phenomenon", mapping, known, psi="PSI-69")
    diffusive = resolve_entity("diffusive extinction", "Phenomenon", mapping, known, psi="PSI-69")
    assert radiative["id"] == "phenomenon:radiative-extinction@PSI-69"
    assert diffusive["id"] == "phenomenon:diffusive-extinction@PSI-69"
    assert resolve_entity("21% O2", "Material/Fuel", mapping, known, psi="PSI-10") is None
    assert resolve_entity("12 mg/s", "Condition", mapping, known, psi="PSI-10") is None
    assert is_forbidden_entity("XO2")
    role = resolve_entity("prevention", "Role", mapping, known, psi="PSI-69")
    assert role is not None
    assert role["id"] == "role:prevention"
    assert "@" not in role["id"]
