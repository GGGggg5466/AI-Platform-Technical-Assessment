from fastapi import APIRouter, UploadFile, File, BackgroundTasks, Query
from app.schemas import JobCreateResponse, JobStatusResponse, ProcessResult, SearchResponse
from app.services.jobs import create_job, run_job, get_job
from app.services.vstore_qdrant import qdrant_search
from app.services.graph_neo4j import graph_find_chunks_by_keyword, graph_fallback_top_chunks
from app.services.llm import call_llm  # ← 用你現有的 LLM wrapper

router = APIRouter()

@router.post("/jobs", response_model=JobCreateResponse)
async def create_job_api(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    route_hint: str | None = Query(default=None, description="Optional: docling/ocr/vlm"),
):
    job_id = await create_job(file=file, route_hint=route_hint)
    background_tasks.add_task(run_job, job_id)
    return JobCreateResponse(job_id=job_id)

@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
def get_job_api(job_id: str):
    job = get_job(job_id)
    return JobStatusResponse(**job)

@router.get("/jobs/{job_id}/result", response_model=ProcessResult)
def get_result_api(job_id: str):
    job = get_job(job_id)
    if job["status"] != "finished":
        return ProcessResult(
            job_id=job_id,
            route=job.get("route") or "docling",
            text_preview="(not finished yet)",
            chunks=0,
            qdrant_points=0,
            lineage_path=job.get("lineage_path") or "",
        )
    return ProcessResult(
        job_id=job_id,
        route=job["route"],
        text_preview=job["text_preview"],
        chunks=job["chunks"],
        qdrant_points=job["qdrant_points"],
        lineage_path=job["lineage_path"],
    )

@router.get("/search", response_model=SearchResponse)
def search_api(q: str = Query(..., min_length=1), limit: int = Query(5, ge=1, le=20)):
    hits = qdrant_search(q, limit=limit)
    return SearchResponse(query=q, hits=hits)


@router.get("/graphrag")
def graphrag(
    keyword: str = Query(...),
    limit: int = 5,
    fallback: int = 5,
):
    hits = graph_find_chunks_by_keyword(keyword, limit=limit)

    used_fallback = False
    if not hits:
        used_fallback = True
        hits = graph_fallback_top_chunks(limit=fallback)

    context = "\n\n".join(
        [f"[{h['filename']}#chunk{h['chunk_id']}]\n{h['text']}" for h in hits]
    )

    prompt = f"""你是一個文件助理。
根據以下內容回答問題；若內容不足請說明。

問題：請解釋與「{keyword}」相關的內容

內容：
{context}
"""

    answer = call_llm(prompt)

    return {
        "keyword": keyword,
        "used_fallback": used_fallback,
        "hits": hits,
        "answer": answer,
    }