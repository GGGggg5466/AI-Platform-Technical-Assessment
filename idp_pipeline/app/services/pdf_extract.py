from pypdf import PdfReader

def extract_pdf_text(pdf_path: str) -> str:
    reader = PdfReader(pdf_path)
    texts = []
    for i, page in enumerate(reader.pages):
        t = page.extract_text() or ""
        if t.strip():
            texts.append(f"\n\n# Page {i+1}\n{t.strip()}")
    return "\n".join(texts) if texts else "(No extractable text. This may be a scanned PDF.)"

def is_scanned_pdf_text(text: str) -> bool:
    """
    Heuristic: if extracted text is empty/too short or contains sentinel,
    we treat it as a scanned/image-based PDF.
    """
    if not text:
        return True
    t = text.strip()
    if len(t) < 80:
        return True
    if "No extractable text" in t:
        return True
    return False

def extract_pdf_pages(pdf_path: str) -> list[dict]:
    """
    return:
      [
        {"page": 1, "text": "..."},
        {"page": 2, "text": "..."},
      ]
    """
    reader = PdfReader(pdf_path)
    out = []
    for i, page in enumerate(reader.pages, start=1):
        t = (page.extract_text() or "").strip()
        out.append({"page": i, "text": t})
    return out