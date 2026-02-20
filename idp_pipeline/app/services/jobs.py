import os, uuid, time, re
from typing import Optional
from fastapi import UploadFile

from app.services.config import DATA_DIR
from app.services.router import choose_route
from app.services.pdf_extract import extract_pdf_pages
from app.services.ocr_olm import ocr_image_via_olm
from app.services.vlm import vlm_extract_markdown
from app.services.chunker import chunk_text
from app.services.embeddings import embed_texts
from app.services.vstore_qdrant import ensure_collection, upsert_chunks
from app.services.lineage import write_lineage, build_page_info_for_pdf
from app.services.pdf_to_images import pdf_to_pngs
from app.services.graph_neo4j import upsert_doc_and_chunks

UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
LINEAGE_DIR = os.path.join(DATA_DIR, "lineage")
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(LINEAGE_DIR, exist_ok=True)

_JOBS: dict[str, dict] = {}

def get_job(job_id: str) -> dict:
    if job_id not in _JOBS:
        return {
            "job_id": job_id,
            "status": "failed",
            "error": "job_id not found",
        }
    return _JOBS[job_id]

async def create_job(file: UploadFile, route_hint: Optional[str] = None) -> str:
    job_id = uuid.uuid4().hex
    filename = file.filename or f"upload_{job_id}"
    save_path = os.path.join(UPLOAD_DIR, f"{job_id}__{filename}")

    content = await file.read()
    with open(save_path, "wb") as f:
        f.write(content)

    _JOBS[job_id] = {
        "job_id": job_id,
        "status": "queued",
        "filename": filename,
        "path": save_path,
        "route_hint": route_hint,
        "created_at": time.time(),
    }
    return job_id

def run_job(job_id: str) -> None:
    job = get_job(job_id)
    if job.get("status") in ("running", "finished"):
        return

    t0 = time.time()

    # ---- 基本欄位（一定存在）----
    path = job.get("path")
    filename = job.get("filename") or f"upload_{job_id}"
    route_hint = job.get("route_hint")

    # ---- 防止「try 前先炸 → 永遠 running」：所有變數先初始化 ----
    route: Optional[str] = None
    raw_text: str = ""
    chunks: list[str] = []
    per_chunk_meta: list[dict] = []
    chunks_payload: list[dict] = []
    point_ids: list[str] = []
    page_info: Optional[dict] = None

    # PDF page-level meta（用於 lineage + per-chunk meta）
    pages_meta: list[dict] = []
    scanned_pdf_detected: bool = False
    images_dir: Optional[str] = None

    # ---- helpers（放 try 前面，避免 UnboundLocalError）----
    MIN_TEXT_CHARS = 20
    OCR_MIN_SCORE = 0.55

    def looks_like_table(t: str) -> bool:
        """
        粗略判斷文字像「表格/欄位對齊」：用來決定是否強制走 VLM。
        """
        t = (t or "").strip()
        if not t:
            return False
        lines = [ln for ln in t.splitlines() if ln.strip()]
        if len(lines) < 4:
            return False

        # 空白對齊 / 多欄位
        col_like = sum(1 for ln in lines if len(re.findall(r"\s{2,}", ln)) >= 2) >= 2
        # 數字比例偏高（常見於表格）
        digit_ratio = sum(ch.isdigit() for ch in t) / max(1, len(t))
        return col_like and digit_ratio > 0.15

    def assess_ocr_quality(text: str) -> float:
        """
        0~1: 越高越像正常可讀文字
        """
        if not text:
            return 0.0
        t = text.strip()
        if not t:
            return 0.0
        if len(t) < 50:
            return 0.1

        bad_chars = sum(1 for ch in t if ch in {"�"} or ord(ch) < 9)
        bad_ratio = bad_chars / max(1, len(t))

        cjk = sum(1 for ch in t if "\u4e00" <= ch <= "\u9fff")
        cjk_ratio = cjk / max(1, len(t))

        score = 0.7 * (1 - bad_ratio) + 0.3 * min(1.0, cjk_ratio * 3)
        return max(0.0, min(1.0, score))

    try:
        # ---- 讓狀態更新一定被 except 捕捉 ----
        job["status"] = "running"
        job["error"] = None
        job["route"] = None
        job["chunks"] = None
        job["qdrant_points"] = None
        job["lineage_path"] = None
        job["text_preview"] = None
        job["updated_at"] = time.time()
        job["stage"] = "route"

        if not path or not os.path.exists(path):
            raise FileNotFoundError(f"input file not found: {path}")

        route = choose_route(path, filename, route_hint=route_hint)
        job["route"] = route
        print("[run_job] start", job_id, route)

        # =========================
        # 1) Extract (PDF/docling + per-page fallback)
        # =========================
        job["stage"] = "extract"

        if route == "docling":
            pages = extract_pdf_pages(path)  # [{'page':1,'text':...}, ...]
            if not pages:
                pages = [{"page": 1, "text": ""}]

            # 決定是否需要把 PDF 轉圖（逐頁 OCR/VLM 需要）
            # 規則：該頁文字太少 or 像表格 → 需要圖片做 VLM 強化
            per_page_need_image = []
            for p in pages:
                base_text = (p.get("text") or "").strip()
                need_img = (len(base_text) < MIN_TEXT_CHARS) or looks_like_table(base_text)
                per_page_need_image.append(need_img)

            page_imgs: list[str] = []
            if any(per_page_need_image):
                stem = os.path.splitext(filename)[0]
                images_dir = os.path.join(UPLOAD_DIR, f"{job_id}__{stem}_images")
                # pdf_to_pngs 會負責建立資料夾（你原本也這樣用）
                page_imgs = pdf_to_pngs(path, out_dir=images_dir, dpi=200)

            # 組 raw_text（用 # Page N marker，方便 trace）
            parts: list[str] = []
            offset = 0
            page_text_start: dict[int, int] = {}  # 每頁「內容」起始位置（不含 marker）
            page_text_end: dict[int, int] = {}

            scanned_pdf_detected = False
            pages_meta = []

            for idx, p in enumerate(pages):
                page_no = int(p.get("page") or (idx + 1))
                base_text = (p.get("text") or "").strip()

                img_path = page_imgs[page_no - 1] if (page_imgs and page_no - 1 < len(page_imgs)) else None
                used = "docling"
                ocr_score = None
                final_text = base_text

                # 逐頁 fallback：
                # A) base_text 太少：OCR → 品質差則 VLM
                # B) base_text 像表格：有圖片就直接 VLM（強化表格 markdown）
                if img_path and looks_like_table(base_text):
                    try:
                        final_text = (vlm_extract_markdown(img_path) or "").strip()
                        used = "vlm"
                    except Exception:
                        final_text = base_text
                        used = "docling"

                elif img_path and len(base_text) < MIN_TEXT_CHARS:
                    scanned_pdf_detected = True
                    ocr_text = ""
                    try:
                        ocr_text = (ocr_image_via_olm(img_path) or "").strip()
                    except Exception:
                        ocr_text = ""

                    score = assess_ocr_quality(ocr_text)
                    ocr_score = round(score, 3)

                    if score < OCR_MIN_SCORE or looks_like_table(ocr_text):
                        try:
                            final_text = (vlm_extract_markdown(img_path) or "").strip()
                            used = "vlm"
                        except Exception:
                            final_text = ocr_text
                            used = "ocr"
                    else:
                        final_text = ocr_text
                        used = "ocr"

                # marker + content
                marker = f"# Page {page_no}\n"
                content = (final_text or "").strip()
                block = marker + content + "\n\n"

                # 記錄頁內容區段在 raw_text 的 offset（方便 chunk start/end 推估）
                page_text_start[page_no] = offset + len(marker)
                page_text_end[page_no] = offset + len(marker) + len(content)
                offset += len(block)

                parts.append(block)

                pages_meta.append({
                    "page": page_no,
                    "text_chars": len(content),
                    "is_scanned": (len(base_text) < MIN_TEXT_CHARS),
                    "image": img_path,
                    "used_route": used,      # docling / ocr / vlm
                    "ocr_score": ocr_score,  # None or float
                })

            raw_text = ("".join(parts)).strip() + "\n"

            # page_info（lineage 需要）
            # 後面 chunk 完再補 chunk_ids
            page_info = {
                "total_pages": len(pages_meta),
                "scanned_pdf_detected": scanned_pdf_detected,
                "images_dir": images_dir,
                "pages": [
                    {
                        "page": m["page"],
                        "text_chars": m["text_chars"],
                        "is_scanned": m["is_scanned"],
                        "image": m["image"],
                        "used_route": m["used_route"],
                        "ocr_score": m["ocr_score"],
                        "chunk_ids": [],
                    }
                    for m in pages_meta
                ],
            }

        elif route == "ocr":
            # 非 PDF 圖片 OCR
            raw_text = (ocr_image_via_olm(path) or "").strip()
            if not raw_text:
                # OCR 空 → 轉 VLM
                route = "vlm"
                job["route"] = route
                raw_text = (vlm_extract_markdown(path) or "").strip()

        else:  # vlm
            raw_text = (vlm_extract_markdown(path) or "").strip()

        raw_text = raw_text or ""

        # =========================
        # 2) Chunking（逐頁切片 + 產生 start/end/page）
        # =========================
        job["stage"] = "chunking"
        print("[run_job] chunking...")

        # 走 docling 時：用 pages_meta 的每頁結果 chunk（page 天然正確）
        if route in ("docling",) and pages_meta:
            # 建一個 quick lookup（page -> used_route/ocr_score/image）
            page_meta_map = {
                m["page"]: {
                    "used_route": m.get("used_route"),
                    "ocr_score": m.get("ocr_score"),
                    "image": m.get("image"),
                }
                for m in pages_meta
            }

            # 逐頁 chunk，並用 page_text_start 做 start/end 推估
            chunk_id = 0
            for m in pages_meta:
                page_no = m["page"]

                # 找出該頁內容（直接從 raw_text 切割，比用 p['text'] 更一致）
                start_pos = page_text_start.get(page_no)
                end_pos = page_text_end.get(page_no)
                if start_pos is None or end_pos is None or end_pos <= start_pos:
                    continue

                page_text = raw_text[start_pos:end_pos].strip()
                if not page_text:
                    continue

                cks = chunk_text(page_text, chunk_size=800, overlap=120)

                # 用 anchor 在 page_text 內找位置（從 cursor 往後找，避免抓到前一段重複）
                cursor = 0
                for ck in cks:
                    ck = ck or ""
                    if not ck.strip():
                        continue

                    anchor = ck[:60]
                    idx = page_text.find(anchor, cursor)
                    if idx < 0:
                        idx = cursor

                    # start/end 轉回 raw_text global offset
                    start = start_pos + idx
                    end = start + len(ck)
                    cursor = max(cursor, idx + len(ck))

                    chunks.append(ck)
                    per_chunk_meta.append({
                        "chunk_id": chunk_id,
                        "chunk_index": chunk_id,
                        "page": page_no,
                        "start": start,
                        "end": end,
                        **page_meta_map.get(page_no, {}),
                    })
                    chunk_id += 1

            # page_info 補 chunk_ids
            if page_info and "pages" in page_info:
                page_idx = {p["page"]: p for p in page_info["pages"]}
                for cm in per_chunk_meta:
                    p = cm["page"]
                    if p in page_idx:
                        page_idx[p]["chunk_ids"].append(cm["chunk_id"])

        else:
            # 非 PDF/或沒有 pages_meta：當作 single page
            # 讓 page=1，start/end 用 raw_text anchor 推估
            cks = chunk_text(raw_text, chunk_size=800, overlap=120)
            cursor = 0
            for i, ck in enumerate(cks):
                ck = ck or ""
                if not ck.strip():
                    continue
                anchor = ck[:60]
                idx = raw_text.find(anchor, cursor)
                if idx < 0:
                    idx = cursor
                start = idx
                end = start + len(ck)
                cursor = max(cursor, idx + len(ck))

                chunks.append(ck)
                per_chunk_meta.append({
                    "chunk_id": i,
                    "chunk_index": i,
                    "page": 1,
                    "start": start,
                    "end": end,
                })

            if page_info is None:
                page_info = {
                    "total_pages": 1,
                    "scanned_pdf_detected": False,
                    "images_dir": None,
                    "pages": [{
                        "page": 1,
                        "text_chars": len(raw_text.strip()),
                        "is_scanned": False,
                        "image": None,
                        "used_route": route,
                        "ocr_score": None,
                        "chunk_ids": [m["chunk_id"] for m in per_chunk_meta],
                    }],
                }

        if not chunks:
            # 沒 chunk 直接結束，避免後面 embedding/upsert 空跑
            job["status"] = "finished"
            job["chunks"] = 0
            job["qdrant_points"] = 0
            job["text_preview"] = raw_text[:300]
            lineage_path = write_lineage(
                job_id=job_id,
                filename=filename,
                route=route or "unknown",
                input_path=path,
                chunk_count=0,
                qdrant_points=0,
                elapsed_sec=round(time.time() - t0, 3),
                chunks=[],
                page_info=page_info,
            )
            job["lineage_path"] = lineage_path
            job["updated_at"] = time.time()
            return

        # =========================
        # 3) Embedding + Qdrant upsert
        # =========================
        job["stage"] = "embedding"
        ensure_collection()

        vectors = embed_texts(chunks)

        job["stage"] = "qdrant_upsert"
        point_ids = upsert_chunks(
            chunks=chunks,
            vectors=vectors,
            meta={"job_id": job_id, "filename": filename, "route": route},
            per_chunk_meta=per_chunk_meta,
        )

        # =========================
        # 4) Build chunks_payload + Neo4j
        # =========================
        job["stage"] = "neo4j"
        # 保底：如果回傳點數比 chunks 少，補齊
        if len(point_ids) < len(chunks):
            point_ids = list(point_ids) + [None] * (len(chunks) - len(point_ids))

        for i in range(len(chunks)):
            pid = point_ids[i]
            cm = per_chunk_meta[i]
            chunks_payload.append({
                "chunk_id": i,
                "text": chunks[i],
                "qdrant_point_id": (str(pid) if pid is not None else None),
                "page": cm.get("page"),
                "start": cm.get("start"),
                "end": cm.get("end"),
            })

        upsert_doc_and_chunks(
            job_id=job_id,
            filename=filename,
            input_path=path,
            route=route or "unknown",
            chunks=chunks_payload
        )

        # =========================
        # 5) Lineage
        # =========================
        job["stage"] = "lineage"
        if page_info is None and path.endswith(".pdf"):
            # 你原本也有這條路徑，保留相容性
            page_info = build_page_info_for_pdf(path, images_dir=images_dir)

        lineage_path = write_lineage(
            job_id=job_id,
            filename=filename,
            route=route or "unknown",
            input_path=path,
            chunk_count=len(chunks),
            qdrant_points=len(point_ids),
            elapsed_sec=round(time.time() - t0, 3),
            chunks=chunks_payload,
            page_info=page_info,
        )

        # =========================
        # 6) Done
        # =========================
        job["status"] = "finished"
        job["chunks"] = len(chunks)
        job["qdrant_points"] = len(point_ids)
        job["lineage_path"] = lineage_path
        job["text_preview"] = (raw_text[:300] + "...") if len(raw_text) > 300 else raw_text
        job["updated_at"] = time.time()
        job["stage"] = "finished"
        print("[run_job] finished", job_id)

    except Exception as e:
        job["status"] = "failed"
        job["error"] = f"{type(e).__name__}: {e}"
        job["updated_at"] = time.time()
        job["stage"] = "failed"
        print("[run_job] failed", job_id, repr(e))
