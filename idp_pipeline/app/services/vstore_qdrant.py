from typing import List, Dict, Any, Optional, Sequence
from qdrant_client import QdrantClient
from qdrant_client.http import models as qm
import uuid

from app.services.config import QDRANT_URL, QDRANT_COLLECTION, QDRANT_VECTOR_SIZE
from app.services.embeddings import embed_texts

_client = QdrantClient(url=QDRANT_URL)

def ensure_collection() -> None:
    existing = [c.name for c in _client.get_collections().collections]
    if QDRANT_COLLECTION in existing:
        return

    _client.create_collection(
        collection_name=QDRANT_COLLECTION,
        vectors_config=qm.VectorParams(
            size=QDRANT_VECTOR_SIZE,
            distance=qm.Distance.COSINE,
        ),
    )

def upsert_chunks(
    chunks: List[str],
    vectors: List[List[float]],
    meta: Dict[str, Any],
    per_chunk_meta: Optional[Sequence[Dict[str, Any]]] = None,
    batch_size: int = 128,
    wait: bool = True,
) -> List[str]:
    """
    Upsert chunks + vectors into Qdrant.

    - chunks / vectors 必須等長
    - meta 會寫進每個 point 的 payload（job_id / filename / route 等）
    - per_chunk_meta 若提供，會「逐 chunk」merge 到 payload（例如 page / used_route / ocr_score / image...）
    - 回傳每個 chunk 對應的 qdrant point id（字串）
    """
    if len(chunks) != len(vectors):
        raise ValueError(f"chunks/vectors length mismatch: {len(chunks)} != {len(vectors)}")

    # per_chunk_meta 可選；若長度不足就當作沒有
    if per_chunk_meta is not None and len(per_chunk_meta) != len(chunks):
        # 不直接 raise，避免整條 pipeline 因為 meta 長度小錯就死掉
        # 改成「能用多少用多少」
        pass

    job_id = str(meta.get("job_id", "job"))
    ids: List[str] = []

    def _do_upsert(points: List[qm.PointStruct]) -> None:
        # qdrant-client 版本差異：有的支援 wait 參數，有的沒有
        try:
            _client.upsert(collection_name=QDRANT_COLLECTION, points=points, wait=wait)
        except TypeError:
            _client.upsert(collection_name=QDRANT_COLLECTION, points=points)

    buf: List[qm.PointStruct] = []

    for idx, (text, vec) in enumerate(zip(chunks, vectors)):
        # 穩定可重現的 id（同 job 同 idx 會固定）
        pid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{job_id}-{idx}"))
        ids.append(pid)

        payload: Dict[str, Any] = {
            **meta,
            "chunk_index": idx,
            "text": text,
        }

        if per_chunk_meta is not None and idx < len(per_chunk_meta):
            extra = per_chunk_meta[idx] or {}
            if isinstance(extra, dict):
                # per-chunk 欄位覆蓋/補上
                payload.update(extra)

        buf.append(qm.PointStruct(id=pid, vector=vec, payload=payload))

        if len(buf) >= batch_size:
            _do_upsert(buf)
            buf.clear()

    if buf:
        _do_upsert(buf)

    return ids

def qdrant_search(query: str, limit: int = 5) -> List[Dict[str, Any]]:
    ensure_collection()
    qvec = embed_texts([query])[0]
    res = _client.search(
        collection_name=QDRANT_COLLECTION,
        query_vector=qvec,
        limit=limit,
        with_payload=True,
    )

    hits = []
    for r in res:
        hits.append({
            "score": float(r.score),
            "id": r.id,
            "payload": r.payload,
        })
    return hits
