import os
from typing import Optional, Literal

Route = Literal["docling", "ocr", "vlm"]

def choose_route(path: str, filename: str, route_hint: Optional[str] = None) -> Route:
    # 1) 手動指定優先（方便 debug / demo）
    if route_hint in ("docling", "ocr", "vlm"):
        return route_hint  # type: ignore

    ext = os.path.splitext(filename.lower())[1]

    # 2) 基本規則：pdf -> docling，image -> ocr
    if ext == ".pdf":
        return "docling"
    if ext in (".png", ".jpg", ".jpeg", ".webp", ".bmp"):
        return "ocr"

    # 3) 其他格式：先走 VLM（保守做法）
    return "vlm"
