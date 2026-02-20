import os
import fitz  # PyMuPDF

def pdf_first_page_to_png(pdf_path: str, out_dir: str, dpi: int = 200) -> str:
    """
    Convert the first page of a PDF into a PNG image.
    Returns the output image path.
    """
    os.makedirs(out_dir, exist_ok=True)
    doc = fitz.open(pdf_path)
    page = doc.load_page(0)
    pix = page.get_pixmap(dpi=dpi)
    out_path = os.path.join(out_dir, "page_1.png")
    pix.save(out_path)
    doc.close()
    return out_path

def pdf_to_pngs(pdf_path: str, out_dir: str, dpi: int = 200) -> list[str]:
    """
    Render ALL pages of a PDF to PNG files.
    Returns: [page_1.png, page_2.png, ...] (absolute/relative paths depending on out_dir)
    """
    os.makedirs(out_dir, exist_ok=True)

    try:
        import fitz  # PyMuPDF
    except Exception as e:
        raise RuntimeError("Missing dependency: PyMuPDF (fitz). Install: pip install pymupdf") from e

    doc = fitz.open(pdf_path)
    paths: list[str] = []

    for i in range(doc.page_count):
        page = doc.load_page(i)
        pix = page.get_pixmap(dpi=dpi)
        out_path = os.path.join(out_dir, f"page_{i+1}.png")
        pix.save(out_path)
        paths.append(out_path)

    doc.close()
    return paths