from pydantic import BaseModel, Field
from typing import Optional, List, Literal, Dict, Any

RouteName = Literal["docling", "ocr", "vlm"]

class JobCreateResponse(BaseModel):
    job_id: str

class JobStatusResponse(BaseModel):
    job_id: str
    status: Literal["queued", "running", "finished", "failed"]
    route: Optional[RouteName] = None
    filename: Optional[str] = None
    error: Optional[str] = None
    chunks: Optional[int] = None
    qdrant_points: Optional[int] = None

class ProcessResult(BaseModel):
    job_id: str
    route: RouteName
    text_preview: str
    chunks: int
    qdrant_points: int
    lineage_path: str

class SearchResponse(BaseModel):
    query: str
    hits: List[Dict[str, Any]] = Field(default_factory=list)