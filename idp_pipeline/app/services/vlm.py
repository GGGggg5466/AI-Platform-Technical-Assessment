import base64
import requests
from app.services.config import VLM_API_URL, VLM_MODEL

def _to_data_url(image_bytes: bytes, mime: str) -> str:
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    return f"data:{mime};base64,{b64}"

def vlm_extract_markdown(image_path: str) -> str:
    with open(image_path, "rb") as f:
        content = f.read()

    data_url = _to_data_url(content, mime="image/png")

    payload = {
        "model": VLM_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "請理解這份文件/圖片內容，輸出結構化 Markdown（保留標題、列表、表格）。"},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
        "temperature": 0.2,
    }

    r = requests.post(VLM_API_URL, json=payload, timeout=180)
    r.raise_for_status()
    j = r.json()
    return j["choices"][0]["message"]["content"]
