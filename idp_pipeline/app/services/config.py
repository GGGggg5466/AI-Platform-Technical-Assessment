import os
from dotenv import load_dotenv

load_dotenv()

def env(key: str, default: str | None = None) -> str:
    v = os.getenv(key)
    if v is None or v == "":
        if default is None:
            raise RuntimeError(f"Missing env var: {key}")
        return default
    return v

DATA_DIR = env("DATA_DIR", "./data")

OLM_API_URL = env("OLM_API_URL", "https://ws-01.olmocr.huannago.com/v1/chat/completions")
OLM_MODEL = env("OLM_MODEL", "allenai/olmOCR-2-7B-1025-FP8")

VLM_API_URL = env("VLM_API_URL", "https://ws-02.wade0426.me/v1/chat/completions")
VLM_MODEL = env("VLM_MODEL", "gemma-3-27b-it")

LLM_API_URL = env("LLM_API_URL", "https://ws-03.wade0426.me/v1/chat/completions")
LLM_MODEL = env("LLM_MODEL", "/models/Qwen3-30B-A3B-Instruct-2507-FP8")

EMBED_API_URL = env("EMBED_API_URL", "https://ws-04.wade0426.me/embed")
EMBED_TASK_DESCRIPTION = env("EMBED_TASK_DESCRIPTION", "檢索技術文件")
EMBED_NORMALIZE = env("EMBED_NORMALIZE", "true").lower() == "true"

QDRANT_URL = env("QDRANT_URL", "http://localhost:6333")
QDRANT_COLLECTION = env("QDRANT_COLLECTION", "idp_docs")
QDRANT_VECTOR_SIZE = int(env("QDRANT_VECTOR_SIZE", "1024"))
