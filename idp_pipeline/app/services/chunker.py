from typing import List

def chunk_text(text: str, chunk_size: int = 800, overlap: int = 120) -> List[str]:
    text = (text or "").strip()
    if not text:
        return []

    chunks: List[str] = []
    i = 0
    n = len(text)

    while i < n:
        j = min(i + chunk_size, n)
        chunk = text[i:j].strip()
        if chunk:
            chunks.append(chunk)
        if j >= n:
            break
        i = max(j - overlap, i + 1)

    return chunks
