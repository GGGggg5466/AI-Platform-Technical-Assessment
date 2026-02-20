import base64
import requests, time
from app.services.config import OLM_API_URL, OLM_MODEL

def _to_data_url(image_bytes: bytes, mime: str) -> str:
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    return f"data:{mime};base64,{b64}"

def ocr_image_via_olm(image_path: str) -> str:
    with open(image_path, "rb") as f:
        content = f.read()

    # NOTE: 不同服務的 multimodal 格式可能略有差異。
    # 這裡用「OpenAI 風格 image_url」的通用寫法。
    data_url = _to_data_url(content, mime="image/png")

    payload = {
        "model": OLM_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "請對這張圖片做 OCR，輸出乾淨的純文字（不要多餘解釋）。"},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
        "temperature": 0.0,
    }

    r = post_with_retry(OLM_API_URL, json=payload, timeout=60, tries=3, base_sleep=1.0)
    j = r.json()

    # OpenAI chat.completions 常見路徑：
    # choices[0].message.content
    return j["choices"][0]["message"]["content"]

def post_with_retry(url: str, json: dict, timeout: int = 60, tries: int = 3, base_sleep: float = 1.0):
    """
    最小重試：針對 502/503/504 做重試（外部服務不穩）
    """
    last_err = None
    for i in range(tries):
        try:
            r = requests.post(url, json=json, timeout=timeout)
            r.raise_for_status()
            return r
        except requests.HTTPError as e:
            code = e.response.status_code if e.response is not None else None
            # upstream gateway 類錯誤：可重試
            if code in (502, 503, 504) and i < tries - 1:
                time.sleep(base_sleep * (2 ** i))  # 1s, 2s, 4s...
                last_err = e
                continue
            raise
        except requests.RequestException as e:
            # 網路類錯誤也可重試
            if i < tries - 1:
                time.sleep(base_sleep * (2 ** i))
                last_err = e
                continue
            raise

    # 理論上不會走到這裡，保底
    raise last_err if last_err else RuntimeError("post_with_retry failed without exception")