"""Retrieval-augmented generation over the school's own documents.

Storage choice
--------------
Embeddings are kept in a ``JSONField`` and scored in Python with numpy rather
than in a vector database. For a surf school's corpus — manuals, policies,
safety procedures, help articles: hundreds of chunks, not millions — a brute
force dot product over a few thousand vectors takes single-digit milliseconds,
and it keeps the deployment to "install Python, run the server". If the corpus
ever outgrows that, only :func:`search` changes.

Dimension safety
----------------
A 2048-dimension NVIDIA vector and a 768-dimension local vector are not
comparable. Every chunk records the model and width it was built with, and
:func:`search` only compares chunks that match the query embedding's model.
Mixing them would produce confident nonsense, so it is refused explicitly.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import numpy as np
from django.utils import timezone
from django.utils.translation import gettext as _

from .models import RagChunk, RagDocument
from .router import get_router

logger = logging.getLogger("apps.ai")

#: Roughly 4 characters per token; 1500 characters ≈ 375 tokens per chunk.
CHUNK_TARGET_CHARS = 1500
CHUNK_OVERLAP_CHARS = 200
MIN_CHUNK_CHARS = 80


@dataclass
class SearchHit:
    chunk_id: int
    document_id: int
    document_title: str
    source_type: str
    content: str
    score: float
    chunk_index: int


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------
def split_into_chunks(
    text: str, target: int = CHUNK_TARGET_CHARS, overlap: int = CHUNK_OVERLAP_CHARS
) -> list[str]:
    """Split *text* on paragraph boundaries, keeping chunks near *target* size.

    Splitting on structure rather than a fixed character count keeps a procedure
    step or a table row intact, which materially improves retrieval quality.
    """
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= target:
        return [text]

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    current = ""

    for paragraph in paragraphs:
        # A single oversized paragraph is split on sentence boundaries.
        if len(paragraph) > target:
            if current:
                chunks.append(current)
                current = ""
            sentences = re.split(r"(?<=[.!?…])\s+", paragraph)
            buffer = ""
            for sentence in sentences:
                if len(buffer) + len(sentence) + 1 > target and buffer:
                    chunks.append(buffer.strip())
                    buffer = buffer[-overlap:] if overlap else ""
                buffer = f"{buffer} {sentence}".strip()
            if buffer:
                current = buffer
            continue

        if len(current) + len(paragraph) + 2 > target and current:
            chunks.append(current)
            tail = current[-overlap:] if overlap else ""
            current = f"{tail}\n\n{paragraph}".strip()
        else:
            current = f"{current}\n\n{paragraph}".strip() if current else paragraph

    if current:
        chunks.append(current)

    return [c for c in chunks if len(c) >= MIN_CHUNK_CHARS] or [text[:target]]


# ---------------------------------------------------------------------------
# Indexing
# ---------------------------------------------------------------------------
def index_document(document: RagDocument, *, force: bool = False) -> tuple[bool, str]:
    """Embed and store every chunk of *document*.

    Returns ``(ok, message)``; never raises, because indexing runs from the UI
    and from background tasks where an exception would be unhelpful.
    """
    if document.is_indexed and not force:
        return True, _("Already indexed.")

    pieces = split_into_chunks(document.content)
    if not pieces:
        return False, _("The document has no indexable text.")

    router = get_router()
    response = router.embed(pieces, input_type="passage")
    if not response.ok:
        return False, response.error or _("No embedding provider is available.")
    if len(response.vectors) != len(pieces):
        return False, _("The embedding provider returned an unexpected number of vectors.")

    RagChunk.objects.filter(document=document).delete()

    rows = []
    for position, (text, vector) in enumerate(zip(pieces, response.vectors)):
        array = np.asarray(vector, dtype=np.float32)
        norm = float(np.linalg.norm(array)) or 1.0
        rows.append(
            RagChunk(
                document=document,
                chunk_index=position,
                content=text,
                token_estimate=max(1, len(text) // 4),
                embedding=[float(v) for v in vector],
                embedding_model=response.model,
                embedding_dimensions=len(vector),
                embedding_norm=norm,
            )
        )
    RagChunk.objects.bulk_create(rows, batch_size=100)

    RagDocument.objects.filter(pk=document.pk).update(
        is_indexed=True, indexed_at=timezone.now(), chunk_count=len(rows)
    )
    logger.info(
        "Indexed RAG document",
        extra={"document": document.title, "chunks": len(rows), "model": response.model},
    )
    return True, _("Indexed %(n)s chunk(s) with %(model)s.") % {
        "n": len(rows),
        "model": response.model,
    }


def reindex_all(*, force: bool = False) -> dict:
    """Re-embed every active document. Returns a small report."""
    indexed = failed = 0
    errors: list[str] = []
    for document in RagDocument.objects.filter(is_active=True):
        ok, message = index_document(document, force=force)
        if ok:
            indexed += 1
        else:
            failed += 1
            errors.append(f"{document.title}: {message}")
    return {"indexed": indexed, "failed": failed, "errors": errors[:10]}


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------
def search(query: str, *, limit: int = 5, min_score: float = 0.25) -> list[SearchHit]:
    """Return the most relevant chunks for *query* (empty list on any failure)."""
    query = (query or "").strip()
    if not query:
        return []

    router = get_router()
    response = router.embed([query], input_type="query")
    if not response.ok or not response.vectors:
        logger.info("RAG search skipped: %s", response.error)
        return []

    query_vector = np.asarray(response.vectors[0], dtype=np.float32)
    query_norm = float(np.linalg.norm(query_vector)) or 1.0

    # Only compare against chunks built by the same embedding model — vectors
    # from different models are not in the same space.
    candidates = list(
        RagChunk.objects.filter(
            embedding_model=response.model,
            embedding_dimensions=len(query_vector),
            document__is_active=True,
        ).select_related("document")
    )

    if not candidates:
        total = RagChunk.objects.count()
        if total:
            logger.warning(
                "RAG index was built with a different embedding model — reindex required.",
                extra={"query_model": response.model, "indexed_chunks": total},
            )
        return []

    matrix = np.asarray([c.embedding for c in candidates], dtype=np.float32)
    norms = np.asarray([c.embedding_norm or 1.0 for c in candidates], dtype=np.float32)
    scores = (matrix @ query_vector) / (norms * query_norm)

    order = np.argsort(-scores)[: max(limit * 3, limit)]
    hits: list[SearchHit] = []
    for index in order:
        score = float(scores[index])
        if score < min_score:
            continue
        chunk = candidates[int(index)]
        hits.append(
            SearchHit(
                chunk_id=chunk.pk,
                document_id=chunk.document_id,
                document_title=chunk.document.title,
                source_type=chunk.document.source_type,
                content=chunk.content,
                score=round(score, 4),
                chunk_index=chunk.chunk_index,
            )
        )
        if len(hits) >= limit:
            break
    return hits


def build_context(hits: list[SearchHit], max_chars: int = 6000) -> tuple[str, list[dict]]:
    """Render hits as prompt context plus a citation list for the UI."""
    if not hits:
        return "", []

    blocks: list[str] = []
    citations: list[dict] = []
    used = 0

    for position, hit in enumerate(hits, start=1):
        block = f"[{position}] {hit.document_title}\n{hit.content}"
        if used + len(block) > max_chars:
            break
        blocks.append(block)
        used += len(block)
        citations.append(
            {
                "index": position,
                "document_id": hit.document_id,
                "title": hit.document_title,
                "source_type": hit.source_type,
                "score": hit.score,
            }
        )

    return "\n\n---\n\n".join(blocks), citations


def index_status() -> dict:
    """Summary shown on the AI Control Center."""
    documents = RagDocument.objects.filter(is_active=True)
    chunks = RagChunk.objects.all()
    models_used = sorted(
        {m for m in chunks.values_list("embedding_model", flat=True).distinct() if m}
    )
    return {
        "documents": documents.count(),
        "indexed_documents": documents.filter(is_indexed=True).count(),
        "chunks": chunks.count(),
        "embedding_models": models_used,
        # More than one model in the index means part of it is unsearchable.
        "needs_reindex": len(models_used) > 1,
    }
