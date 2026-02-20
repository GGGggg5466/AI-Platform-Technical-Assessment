import requests
from app.services.config import EMBED_API_URL, EMBED_TASK_DESCRIPTION, EMBED_NORMALIZE

def embed_texts(texts: list[str]) -> list[list[float]]:
    payload = {
        "texts": texts,
        "task_description": EMBED_TASK_DESCRIPTION,
        "normalize": EMBED_NORMALIZE,
    }
    r = requests.post(EMBED_API_URL, json=payload, timeout=120)
    r.raise_for_status()
    j = r.json()
    return j["embeddings"]
