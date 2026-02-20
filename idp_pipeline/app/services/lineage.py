# app/services/lineage.py
from __future__ import annotations
from pathlib import Path

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional


def _now_iso() -> str:
    # e.g. "2026-02-19 22:41:54"
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _safe_preview(text: str, n: int = 200) -> str:
    text = text or ""
    return text[:n] + ("..." if len(text) > n else "")


def _get_chunk_id(ch: Dict[str, Any], default_id: int) -> int:
    """
    容錯：你不同階段可能用 chunk_id / chunk_index / idx
    """
    for k in ("chunk_id", "chunk_index", "idx"):
        if k in ch and ch[k] is not None:
            try:
                return int(ch[k])
            except Exception:
                pass
    return default_id


def _get_point_id(ch: Dict[str, Any]) -> Optional[str]:
    """
    容錯：你不同地方可能叫 qdrant_point_id / point_id
    """
    for k in ("qdrant_point_id", "point_id"):
        v = ch.get(k)
        if v is not None:
            return str(v)
    return None

def build_page_info_for_pdf(pdf_path: str, images_dir: Optional[str] = None) -> Dict[str, Any]:
    """
    以 PDF 真實頁數建立 page_info：
    - text_chars: 該頁可選文字長度（pypdf extract_text）
    - is_scanned: 可選文字過少 -> 視為掃描頁
    - image: 若 scanned，填對應 page_{n}.png（如果 images_dir 有提供且檔案存在）
    """
    from pypdf import PdfReader

    pdf_path = str(pdf_path)
    reader = PdfReader(pdf_path)
    total_pages = len(reader.pages)

    pages: List[Dict[str, Any]] = []
    img_dir = Path(images_dir) if images_dir else None

    for i, page in enumerate(reader.pages, start=1):
        txt = page.extract_text() or ""
        txt_chars = len(txt.strip())
        is_scanned = txt_chars < 20

        image_path = None
        if is_scanned and img_dir:
            candidate = img_dir / f"page_{i}.png"
            if candidate.exists():
                image_path = str(candidate)

        pages.append({
            "page": i,
            "text_chars": txt_chars,
            "is_scanned": is_scanned,
            "image": image_path,
        })

    return {
        "total_pages": total_pages,
        "pages": pages,
    }

def write_lineage(
    *,
    job_id: str,
    filename: Optional[str],
    route: str,
    input_path: str,
    chunk_count: int,
    qdrant_points: int,
    elapsed_sec: float,
    chunks: Optional[List[Dict[str, Any]]] = None,
    page_info: Optional[Dict[str, Any]] = None,
    include_text: bool = True,
    preview_chars: int = 200,
    out_dir: str = "data/lineage",
) -> str:
    """
    產生更完整的 lineage JSON：
    - chunk-level: chunk_id, qdrant_point_id, page, start/end, text_len, preview (+ text 可選)
    - page-level: page_info (由 jobs.py build_page_info() 產生的結果)
    """

    _ensure_dir(out_dir)

    chunks = chunks or []
    normalized_chunks: List[Dict[str, Any]] = []

    for idx, ch in enumerate(chunks):
        # 你 jobs.py 的 chunks_payload 目前有 text/qdrant_point_id/page/start/end
        text = ch.get("text") or ""
        cid = _get_chunk_id(ch, idx)
        pid = _get_point_id(ch)

        page = ch.get("page", None)
        start = ch.get("start", None)
        end = ch.get("end", None)

        item: Dict[str, Any] = {
            "chunk_id": cid,
            "qdrant_point_id": pid,
            "page": page,
            "start": start,
            "end": end,
            "text_len": len(text),
            "preview": _safe_preview(text, preview_chars),
        }

        # 老師要「完整內容」→ 建議 include_text=True
        if include_text:
            item["text"] = text

        normalized_chunks.append(item)

    payload: Dict[str, Any] = {
        "job_id": job_id,
        "filename": filename,
        "route": route,
        "input_path": input_path,
        "chunk_count": chunk_count,
        "qdrant_points": qdrant_points,
        "elapsed_sec": elapsed_sec,
        "created_at": _now_iso(),
        "chunks": normalized_chunks,
        "page_info": page_info,  # 你已經在 jobs.py build_page_info(...) 做好了就塞進來
    }

    out_path = os.path.join(out_dir, f"{job_id}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    return out_path
