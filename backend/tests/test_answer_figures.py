"""Retrieved chunks attach their stored figure files to the ask payload."""

from __future__ import annotations

import base64

from app.orchestrator import format_ask

_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _write_png(root):
    path = root / "data" / "images" / "shot.png"
    path.parent.mkdir(parents=True)
    path.write_bytes(_PNG)
    return path


def test_format_ask_ships_figures_from_the_chunk_list(tmp_path, monkeypatch) -> None:
    _write_png(tmp_path)
    monkeypatch.setattr("app.orchestrator.ROOT", tmp_path)
    packed = format_ask(
        {
            "question": "soot on the droplet",
            "answer": "Soot forms around the droplet.",
            "trace": [],
            "ranked": [],
            "chunks": [
                {
                    "chunkId": "ch-1",
                    "preview": "Soot forms around the droplet in the chamber.",
                    "page": 7,
                    "documentTitle": "Droplet report",
                    "figures": ["data/images/shot.png", "data/images/missing.png"],
                }
            ],
            "communities": [],
            "focus_id": None,
            "mode": "search",
        }
    )
    assert packed["figures"] == [
        {
            "src": "/api/media/images/shot.png",
            "alt": "Droplet report — trang 7",
            "caption": "Droplet report — trang 7",
        }
    ]


def test_format_ask_reads_pdf_figure_marker_when_list_is_empty(tmp_path, monkeypatch) -> None:
    _write_png(tmp_path)
    monkeypatch.setattr("app.orchestrator.ROOT", tmp_path)
    packed = format_ask(
        {
            "question": "flame structure",
            "answer": "The page shows the flame structure.",
            "trace": [],
            "ranked": [],
            "chunks": [
                {
                    "chunkId": "ch-2",
                    "preview": "Structure. [figure: 1 | page: 7 | file: data/images/shot.png]",
                    "page": 7,
                    "psi": "PSI-20",
                    "figures": [],
                }
            ],
            "communities": [],
            "focus_id": None,
            "mode": "search",
        }
    )
    assert packed["figures"][0]["src"] == "/api/media/images/shot.png"
    assert "PSI-20 — trang 7" == packed["figures"][0]["caption"]
