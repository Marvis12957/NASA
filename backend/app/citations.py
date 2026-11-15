"""Deterministic citation normalization for ask answers.

The LLM cites evidence with raw internal ids: fullwidth-bracket markers
(U+3010 ... U+3011) wrapping chunk ids / community ids, and parenthesized
id groups such as (PSI-10/.../p040; PSI-20/.../p007). Those ids mean
nothing to a researcher. This module rewrites them, at answer finalization
time, into numbered references [1], [2], ... and appends a sources section
built ONLY from the chunks / communities / ranked rows retrieved for that
same answer. An id that cannot be resolved against the retrieved evidence
is dropped, never dressed up with invented metadata (no fabricated
titles, DOIs, or pages).
"""

from __future__ import annotations

import os
import re

from app.evidence_filter import is_citable_community, is_garbage_text

_LB = chr(12304)  # fullwidth left bracket U+3010
_RB = chr(12305)  # fullwidth right bracket U+3011
_MASTER_RE = re.compile(
    _LB + "([^" + _RB + "]+)" + _RB + r"|\(([^()]*)\)|\*\*([^*]+)\*\*"
)
_SPLIT_RE = re.compile(r"[,;]")
_PSI_RE = re.compile(r"^PSI-\d+$")
_COMM_SHORT_RE = re.compile(r"^L\d+:\d+$")

_PSI_URL = "https://psi.nasa.gov/physci/repo/data/investigations/{psi}"
_SOURCES_HEADING = "## Nguồn & bằng chứng"


def _doc_title(chunk: dict) -> str | None:
    """Document title from metadata when sane, else from the source filename."""
    title = str(chunk.get("documentTitle") or "").strip()
    if len(title) >= 10 and len(title.split()) >= 2:
        return title
    source = str(chunk.get("source") or "").strip()
    if source:
        base = os.path.basename(source)
        if base.lower().endswith(".pdf"):
            base = base[:-4]
        base = base.strip()
        if base:
            return base
    return None


def _page_label(chunk: dict) -> str | None:
    span = chunk.get("pages_span") or []
    if len(span) == 2 and span[0] and span[1] and span[1] > span[0]:
        return f"trang {span[0]}–{span[1]}"
    if chunk.get("page"):
        return f"trang {chunk['page']}"
    return None


class _Refs:
    """Resolves citation ids against one answer's retrieved evidence."""

    def __init__(self, chunks, communities, ranked):
        self.chunk_map: dict[str, dict] = {}
        for c in chunks or []:
            # Failed-OCR chunks are evidence garbage: never a citation source.
            if is_garbage_text(str(c.get("preview") or "")):
                continue
            cid = str(c.get("chunkId") or "")
            if cid:
                self.chunk_map[cid] = c
        self.comm_map: dict[str, dict] = {}
        for cm in communities or []:
            # A community that names no PSI investigation is a bare theme
            # cluster; it must not appear as a numbered source.
            if not is_citable_community(cm):
                continue
            cid = str(cm.get("id") or "")
            if cid:
                self.comm_map[cid] = cm
        self.inv_map: dict[str, dict] = {}
        for row in ranked or []:
            pid = str(row.get("id") or "")
            if _PSI_RE.match(pid):
                self.inv_map[pid] = {"name": row.get("name"), "doi": row.get("doi")}
        for c in chunks or []:
            if is_garbage_text(str(c.get("preview") or "")):
                continue
            inv = c.get("investigation") or {}
            pid = str(inv.get("id") or c.get("psi") or "")
            if _PSI_RE.match(pid) and pid not in self.inv_map:
                self.inv_map[pid] = {"name": inv.get("name"), "doi": inv.get("doi")}
        self.order: list[tuple[str, str]] = []
        self.numbers: dict[str, int] = {}

    def number(self, key: str, line: str) -> int:
        if key not in self.numbers:
            self.numbers[key] = len(self.order) + 1
            self.order.append((key, line))
        return self.numbers[key]

    def resolve(self, token: str) -> tuple[str, str] | None:
        t = (token or "").strip()
        if not t:
            return None
        if t in self.chunk_map:
            return (f"chunk:{t}", self._chunk_line(self.chunk_map[t]))
        if t in self.comm_map:
            return (f"comm:{t}", self._comm_line(self.comm_map[t]))
        if _COMM_SHORT_RE.match(t) and f"community:{t}" in self.comm_map:
            return (f"comm:community:{t}", self._comm_line(self.comm_map[f"community:{t}"]))
        if _PSI_RE.match(t) and t in self.inv_map:
            return (f"inv:{t}", self._inv_line(t))
        return None

    def _chunk_line(self, chunk: dict) -> str:
        psi = str(chunk.get("psi") or "")
        if not _PSI_RE.match(psi):
            first = str(chunk.get("chunkId") or "").split("/")[0]
            if _PSI_RE.match(first):
                psi = first
        doc = _doc_title(chunk)
        page = _page_label(chunk)
        mid = []
        if doc:
            mid.append(f'tài liệu "{doc}"')
        if page:
            mid.append(page)
        if _PSI_RE.match(psi):
            inv = chunk.get("investigation") or {}
            meta = self.inv_map.get(psi, {})
            name = inv.get("name") or meta.get("name")
            doi = inv.get("doi") or meta.get("doi")
            line = f"{name} ({psi})" if name else psi
            if mid:
                line += " — " + ", ".join(mid)
            line += "."
            if doi:
                line += f" DOI: {_doi_link(doi)}."
            line += " " + _PSI_URL.format(psi=psi)
            return line
        # Shared / web source (no single PSI investigation behind the chunk).
        chunk_id = str(chunk.get("chunkId") or "")
        if chunk_id.startswith("ACME_common") or psi == "ACME_common":
            label = "ACME — tài liệu chung (ACME_common)"
        elif chunk_id.startswith("web/"):
            label = f"Tài liệu chung (web/{chunk_id.split('/')[1]})"
        else:
            label = f"Tài liệu chung ({psi or chunk_id.split('/')[0]})"
        line = label
        if mid:
            line += " — " + ", ".join(mid)
        line += "."
        return line

    def _comm_line(self, comm: dict) -> str:
        short = str(comm.get("id") or "").removeprefix("community:")
        title = str(comm.get("title") or "").strip()
        if title:
            return f'Tổng hợp chủ đề GraphRAG: "{title}" (cộng đồng {short}).'
        return f"Tổng hợp chủ đề GraphRAG (cộng đồng {short})."

    def _inv_line(self, psi: str) -> str:
        meta = self.inv_map.get(psi, {})
        name = meta.get("name")
        doi = meta.get("doi")
        line = f"{name} ({psi})" if name else psi
        line += " — hồ sơ điều tra NASA PSI."
        if doi:
            line += f" DOI: {_doi_link(doi)}."
        line += " " + _PSI_URL.format(psi=psi)
        return line


def _doi_link(doi: str) -> str:
    """DOI as a clickable markdown link, like a paper's reference list."""
    value = str(doi).strip().removeprefix("https://doi.org/").removeprefix("doi:").strip()
    return f"[{value}](https://doi.org/{value})"


def _trails_sentence(text: str, start: int) -> bool:
    """True when a parenthesis opens right after sentence punctuation or at a line start."""
    before = text[:start].rstrip(" \t")
    return not before or before[-1] in ".!?:;\n)]*"


def _looks_like_id(token: str) -> bool:
    t = token.strip()
    return "/" in t or t.startswith("community:") or bool(_COMM_SHORT_RE.match(t))


def normalize_citations(
    text: str,
    *,
    chunks: list[dict] | None = None,
    communities: list[dict] | None = None,
    ranked: list[dict] | None = None,
    cited_chunks: list[dict] | None = None,
) -> str:
    """Rewrite raw evidence ids in `text` as [n] refs + a sources section."""
    if not text:
        return text
    refs = _Refs(chunks, communities, ranked)

    def _note_cited(resolved, tok) -> None:
        # Optional side channel: collect the chunk dicts the answer actually
        # cited (in first-citation order) for downstream payloads (figures).
        if cited_chunks is None or not resolved:
            return
        if resolved[0].startswith("chunk:"):
            chunk = refs.chunk_map.get((tok or "").strip())
            if chunk is not None and all(chunk is not c for c in cited_chunks):
                cited_chunks.append(chunk)

    def sub(match: re.Match) -> str:
        marker, paren, bold = match.group(1), match.group(2), match.group(3)
        if bold is not None:
            # A bold span that is exactly one resolvable evidence id is a
            # citation; any other bold span is prose emphasis — keep it.
            tok = bold.strip()
            if _looks_like_id(tok):
                resolved = refs.resolve(tok)
                if resolved:
                    _note_cited(resolved, tok)
                    return f"[{refs.number(*resolved)}]"
            return match.group(0)
        if marker is not None:
            nums = []
            for tok in _SPLIT_RE.split(marker):
                resolved = refs.resolve(tok)
                if resolved:
                    _note_cited(resolved, tok)
                    n = refs.number(*resolved)
                    if n not in nums:
                        nums.append(n)
            return "".join(f"[{n}]" for n in nums)
        toks = [t for t in _SPLIT_RE.split(paren) if t.strip()]
        if toks and all(_looks_like_id(t) for t in toks):
            # A group of internal chunk/community ids. Resolvable ids become [n];
            # an id that matches no retrieved evidence (e.g. mistyped by the model)
            # is dropped. Raw ids are never shown to the reader.
            nums: list[int] = []
            for tok in toks:
                resolved = refs.resolve(tok)
                if resolved:
                    _note_cited(resolved, tok)
                    n = refs.number(*resolved)
                    if n not in nums:
                        nums.append(n)
            return "".join(f"[{n}]" for n in nums)
        if toks and all(_PSI_RE.match(t.strip()) for t in toks) and _trails_sentence(text, match.start()):
            # "... đứng đầu danh mục. (PSI-102)" cites the catalog record; "SAME (PSI-102)"
            # inside a sentence is prose and stays as written.
            resolved_psi = [refs.resolve(tok) for tok in toks]
            if all(resolved_psi):
                return "".join(f"[{refs.number(*r)}]" for r in resolved_psi)
        return match.group(0)

    out = _MASTER_RE.sub(sub, text)
    out = re.sub(r"[ \t]+([.,;:!?])", r"\1", out)  # space left by a dropped id group
    out = re.sub(r"[ \t]+$", "", out, flags=re.MULTILINE)
    if not refs.order:
        return out
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r" ([.,;:!?])", r"\1", out)
    lines = [f"[{n}] {line}" for n, (_key, line) in enumerate(refs.order, start=1)]
    # One reference per paragraph so each source renders on its own line.
    return out.rstrip() + "\n\n" + _SOURCES_HEADING + "\n\n" + "\n\n".join(lines)
