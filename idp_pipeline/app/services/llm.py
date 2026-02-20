import os, requests

LLM_API_URL = os.getenv("LLM_API_URL")  # 例如 https://ws-03.wade0426.me/v1/chat/completions
LLM_MODEL = os.getenv("LLM_MODEL")      # 例如 /models/Qwen3-30B-A3B-Instruct-2507-FP8

def call_llm(prompt: str) -> str:
    assert LLM_API_URL, "LLM_API_URL not set in .env"
    assert LLM_MODEL, "LLM_MODEL not set in .env"

    payload = {
        "model": LLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
    }
    r = requests.post(LLM_API_URL, json=payload, timeout=(10, 120))
    r.raise_for_status()
    data = r.json()
    return data["choices"][0]["message"]["content"]
