"""Citation rewriting: raw evidence ids never reach the reader."""

from app.citations import normalize_citations

CHUNKS = [
    {
        "chunkId": "PSI-102/same-ices-2013/p001-003",
        "psi": "PSI-102",
        "preview": "Smoke Aerosol Measurement Experiment measured smoke particle size on the ISS.",
        "page": 1,
        "investigation": {"id": "PSI-102", "name": "SAME", "doi": "10.60555/gd4k-8h62"},
    }
]
RANKED = [{"id": "PSI-102", "name": "SAME", "doi": "10.60555/gd4k-8h62"}]


def test_unresolvable_id_is_dropped_not_shown():
    text = "SAME measured smoke. (PSI-102/same-ices-2013/p001-003; PSI-102/made-up-doc/p047)"
    out = normalize_citations(text, chunks=CHUNKS, ranked=RANKED)
    assert "made-up-doc" not in out
    assert "PSI-102/same-ices" not in out.split("## Nguồn")[0]
    assert "[1]" in out
    assert "## Nguồn & bằng chứng" in out


def test_only_unknown_ids_vanish():
    out = normalize_citations("Smoke rises slowly. (web/unknown/c001)", chunks=CHUNKS, ranked=RANKED)
    assert out == "Smoke rises slowly."


def test_trailing_psi_becomes_numbered_source():
    text = "Thí nghiệm quan trọng nhất là SAME (PSI-102), điểm 96. (PSI-102)"
    out = normalize_citations(text, chunks=CHUNKS, ranked=RANKED)
    body, _, sources = out.partition("## Nguồn & bằng chứng")
    assert "SAME (PSI-102)" in body  # prose mention stays
    assert body.rstrip().endswith("[1]")
    assert "SAME (PSI-102) — hồ sơ điều tra NASA PSI" in sources
    assert "https://psi.nasa.gov/physci/repo/data/investigations/PSI-102" in sources
