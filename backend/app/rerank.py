"""Hybrid retrieve (BM25 + cosine RRF) then cross-encoder rerank."""

from __future__ import annotations

import math
import os
import re
from collections import Counter
from functools import lru_cache
from typing import Any, Sequence

CROSS_ENCODER_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"
RRF_K = 60

_TOKEN = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> set[str]:
    return {tok for tok in _TOKEN.findall((text or "").lower()) if len(tok) > 2}


def tokenize_list(text: str) -> list[str]:
    return [tok for tok in _TOKEN.findall((text or "").lower()) if len(tok) > 2]


class BM25Index:
    """In-memory BM25Okapi over (id, text) chunk corpus."""

    def __init__(self, documents: Sequence[tuple[str, str]], *, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.ids = [doc_id for doc_id, _text in documents]
        corpus = [tokenize_list(text) for _doc_id, text in documents]
        self.tf = [Counter(tokens) for tokens in corpus]
        self.doc_len = [max(len(tokens), 1) for tokens in corpus]
        self.n = len(corpus)
        self.avgdl = (sum(self.doc_len) / self.n) if self.n else 0.0
        df: Counter[str] = Counter()
        for tokens in corpus:
            df.update(set(tokens))
        self.idf = {
            term: math.log(1.0 + (self.n - freq + 0.5) / (freq + 0.5))
            for term, freq in df.items()
        }

    def search(self, query: str, limit: int) -> list[tuple[str, float]]:
        q = tokenize_list(query)
        if not q or not self.ids:
            return []
        scores = [0.0] * self.n
        avgdl = self.avgdl or 1.0
        for term in q:
            idf = self.idf.get(term)
            if idf is None:
                continue
            for i, tf in enumerate(self.tf):
                freq = tf.get(term, 0)
                if not freq:
                    continue
                denom = freq + self.k1 * (1.0 - self.b + self.b * self.doc_len[i] / avgdl)
                scores[i] += idf * (freq * (self.k1 + 1.0) / denom)
        ranked = sorted(
            ((self.ids[i], scores[i]) for i in range(self.n) if scores[i] > 0),
            key=lambda item: -item[1],
        )
        return ranked[: max(1, limit)]


def reciprocal_rank_fusion(
    ranked_lists: Sequence[Sequence[str]],
    *,
    k: int = RRF_K,
) -> list[tuple[str, float]]:
    """Merge retrieval lists. Higher fused score is better."""
    fused: dict[str, float] = {}
    for ranking in ranked_lists:
        for rank, doc_id in enumerate(ranking, start=1):
            if not doc_id:
                continue
            fused[doc_id] = fused.get(doc_id, 0.0) + 1.0 / (k + rank)
    return sorted(fused.items(), key=lambda item: -item[1])


def lexical_scores(query: str, documents: list[str]) -> list[float]:
    q = tokenize(query)
    if not q:
        return [0.0] * len(documents)
    scores = []
    for doc in documents:
        d = tokenize(doc)
        if not d:
            scores.append(0.0)
            continue
        scores.append(len(q & d) / float(len(q)))
    return scores


@lru_cache(maxsize=1)
def _cross_encoder():
    try:
        from sentence_transformers import CrossEncoder
    except Exception:  # noqa: BLE001
        return None
    try:
        return CrossEncoder(CROSS_ENCODER_MODEL)
    except Exception:  # noqa: BLE001
        return None


def _want_cross_encoder() -> bool:
    flag = (os.environ.get("RERANK_CROSS_ENCODER") or "1").strip().lower()
    return flag not in {"0", "false", "no", "lexical"}


def cross_encoder_scores(query: str, documents: list[str]) -> list[float] | None:
    if not documents:
        return []
    if not _want_cross_encoder():
        return None
    model = _cross_encoder()
    if model is None:
        return None
    pairs = [(query, doc or "") for doc in documents]
    raw = model.predict(pairs)
    return [float(value) for value in raw]


def rerank_scores(query: str, documents: list[str]) -> tuple[list[float], str]:
    """Return per-document scores and which scorer ran."""
    ce = cross_encoder_scores(query, documents)
    if ce is not None:
        return ce, "cross-encoder"
    return lexical_scores(query, documents), "lexical"


def rerank_records(
    query: str,
    records: list[dict[str, Any]],
    *,
    text_key: str,
    limit: int,
) -> tuple[list[dict[str, Any]], str]:
    if not records:
        return [], "empty"
    documents = [str(item.get(text_key) or item.get("preview") or item.get("summary") or "") for item in records]
    scores, method = rerank_scores(query, documents)
    ranked = []
    for item, score in zip(records, scores):
        row = dict(item)
        row["rerankScore"] = float(score)
        row["rerankMethod"] = method
        ranked.append(row)
    ranked.sort(key=lambda row: -float(row.get("rerankScore") or 0))
    return ranked[: max(1, limit)], method
