# IDP Pipeline（Docling / OCR / VLM）非同步文件處理 API

> 目標：把 **PDF / 圖片** 轉成可用於檢索與 RAG 的結構化資料（chunks + embeddings + Qdrant），並提供 **非同步 API**、**動態路由（Docling/OCR/VLM）**、**lineage 追蹤**、以及 **GraphRAG demo**。
> 
> 
> 部署包含 **Nginx Gateway（rate limit + upstream health）**、**Qdrant**、**Neo4j**。
> 

---

## 1. 系統架構與資料流

### 1.1 架構圖（含 Gateway / Vector DB / Graph DB）

```mermaid
flowchart LR
  C[Client] -->|HTTP| G[Nginx Gateway :8080]
  G -->|proxy_pass| A[FastAPI :8000]

  A -->|extract/ocr/vlm| S[Services Layer]
  S -->|chunks + embeddings| Q[Qdrant :6333]
  S -->|doc+chunk nodes| N[Neo4j :7687]
  S -->|lineage json| L[(data/lineage/*.json)]

  A -->|GET /v1/jobs/{id}| C
  A -->|GET /v1/jobs/{id}/result| C
  A -->|GET /v1/graphrag| C
```

### 1.2 非同步 Job 模型

- `POST /v1/jobs`：上傳 PDF/圖片 → 回傳 `job_id`
- `GET /v1/jobs/{job_id}`：查狀態（queued/running/finished/failed）
- `GET /v1/jobs/{job_id}/result`：拿結果（text_preview / chunks / qdrant_points / lineage_path）

---

## 2. 路由策略（Docling / OCR / VLM）與動態切換

- **PDF**
    - 優先 Docling（可抽到可選取文字就直接用）
    - 若 Docling 抽不到文字（像掃描 PDF）→ **fallback：pdf→images→OCR**
- **Image**
    - 直接 OCR（若 OCR 服務不穩、或品質差）→ fallback VLM
- **route_hint（可手動指定）**
    - `route_hint=docling | ocr | vlm`
    - 若不指定：走預設策略（通常是 auto / router 決策）

> 「第一次 running、第二次 finished」是正常的：
> 
> 
> 因為在 **polling**（輪詢）狀態，worker/背景任務還在跑；下一次查詢就可能已經結束。
> 

---

## 3. 檔案結構與各程式職責

以下以你目前 `idp_pipeline/app` 與 `app/services` 的檔為主（命名對照你檔案總管）：

### 3.1 FastAPI 層

- `app/main.py`
    - FastAPI 啟動點、載入 routes、健康檢查等
- `app/routes.py`
    - API 路由定義：`/v1/jobs`、`/v1/search`、`/v1/graphrag`（GraphRAG demo）等
- `app/schemas.py`
    - Pydantic schemas：JobStatus、Result、SearchResponse…（OpenAPI 會從這裡長出來）

### 3.2 Services 層（核心管線）

- `app/services/router.py`
    - 路由決策：依 input_type（pdf/image/text）+ 品質/可抽取性 + route_hint 來選 `docling/ocr/vlm`
- `app/services/pdf_extract.py`
    - PDF 文字抽取（例如 `pypdf`）；用來判斷是不是 text-based PDF
- `app/services/pdf_to_images.py`
    - PDF 轉圖片（掃描 PDF fallback 會用到）
- `app/services/ocr_olm.py`
    - OCR 呼叫（OLM OCR endpoint）；目前遇到的主要不穩來源（502）
- `app/services/vlm.py`
    - VLM 呼叫（例如 Gemma）；當 OCR 或 Docling 不可靠時的 fallback
- `app/services/chunker.py`
    - 把長文本切成 chunks（chunk_id / chunk text）
- `app/services/embeddings.py`
    - Embedding API 呼叫（把 chunk text → 向量）
- `app/services/vstore_qdrant.py`
    - Qdrant upsert / collection 管理
- `app/services/graph_neo4j.py`
    - Neo4j upsert：Document 與 Chunk 節點 + 關係（HAS_CHUNK）
- `app/services/lineage.py`
    - lineage 寫入：把這次 job 的處理摘要落到 `data/lineage/{job_id}.json`
    - 已完成的「chunk-level lineage」：包含 `chunk_id / qdrant_point_id / page / preview / text_len …`

### 3.3 Gateway / Infra

- `nginx/nginx.conf`
    - Nginx 當 API Gateway：reverse proxy、rate limit（429）、上游健康檢查/故障切換、body size 限制等
- `docker-compose.yml`
    - 起 Neo4j、Qdrant、Gateway（以及你需要的其他容器）

---

## 4. PDF / 圖片測試資料種類

### 4.1 PDF 類型

- `sample_table.pdf`
    - 偏向「掃描或不可抽取文字」的案例（Docling/pypdf 可能抽不到）→ 會走 **fallback OCR**
    - 實測 `is_scanned=True`，所以符合預期：Docling 抽不到就該 fallback
- `text_ok.pdf`
    - 「可選取文字」PDF（pypdf/docling 抽得到）→ Docling 應該可以直接成功

### 4.2 圖片類型

- `clear_text.png`
    - 清楚文字圖片（OCR 應該最理想的測資）
    - 驗證過：route_hint=ocr 時，如果 OCR endpoint 不穩，會被迫 fallback 到 VLM（所以你看到很多最後 route 變 vlm）

---

## 5. API 測試流程（可直接複製）

### 5.1 Health / OpenAPI

```bash
curl -i http://127.0.0.1:8080/health
curl -s http://127.0.0.1:8080/openapi.json |head
```

### 5.2 上傳 PDF → 查狀態 → 拿結果

```bash
# 建 job（PDF）
curl -s -X POST"http://127.0.0.1:8080/v1/jobs" \
  -F"file=@data/uploads/pdf/text_ok.pdf"# 查狀態
curl -s"http://127.0.0.1:8080/v1/jobs/<JOB_ID>"# 拿結果
curl -s"http://127.0.0.1:8080/v1/jobs/<JOB_ID>/result"
```

### 5.3 指定路由 route_hint（強制 docling / ocr / vlm）

```bash
curl -s -X POST"http://127.0.0.1:8080/v1/jobs?route_hint=docling" \
  -F"file=@data/uploads/pdf/text_ok.pdf"

curl -s -X POST"http://127.0.0.1:8080/v1/jobs?route_hint=ocr" \
  -F"file=@data/uploads/images/clear_text.png"

curl -s -X POST"http://127.0.0.1:8080/v1/jobs?route_hint=vlm" \
  -F"file=@data/uploads/images/clear_text.png"
```

### 5.4 查狀態

（建議用 `-G` + `--data-urlencode`）：

```bash
curl -s "http://127.0.0.1:8080/v1/jobs/XXXX"

```

### 5.5 拿結果

```bash
curl -s "http://127.0.0.1:8080/v1/jobs/XXXX/result"
```

### 5.6 驗證 lineage

```bash
cat data/lineage/XXXX.json | head -n 120
```
舉例 : 

-  "job_id": "e19bcc9fc22c452bbb3e4876a84272a1",
-  "filename": "text_ok.pdf",
-  "route": "docling",
-  "input_path": "./data/uploads/e19bcc9fc22c452bbb3e4876a84272a1__text_ok.pdf",
-  "chunk_count": 51,
-  "qdrant_points": 51,
-  "elapsed_sec": 14.267,
-  "created_at": "2026-02-19 20:53:12",
-  "chunks": [
    {
      "chunk_id": 0,
      "qdrant_point_id": "7d784e59-e5ad-5c40-9afa-21cabc291333",
      "page": 1,
      "text_len": 800,
      "preview": "# Page 1\n十二年國民基本教育課程綱要 \n技術型高級中等學校 \n \n \n \n \n \n \n \n科 技 領 域 \n \n \n \n \n \n \n \n \n中華民國一 ○ 七 年 九 月 \n行政院公報 第 024 卷 第 180 期  201809..."
    },
    {
      "chunk_id": 1,
      "qdrant_point_id": "17bc06d4-006d-5d47-9b28-0c1a02856c06",
      "page": 1,
      "text_len": 800,
      "preview": "........... \n二、教材編選 .................................................. \n三、教學實施 ............................................"
    },
    .
    .
    .
---

## 6. 常見問題與實際遇到的坑（含解法）

### 6.1 OCR endpoint 502 / 不穩定

**現象**

- `requests.post(OLM_API_URL)` 回 502 Bad Gateway
- Job status 變 `failed`，error 顯示 OLM URL 502

**原因**

- 上游 OCR 服務本身不穩（你已用 python script 直打驗證，確實是 upstream 502，不是你 API 寫錯）

**解法**

- 在 `ocr_olm.py` 實作 `post_with_retry()`：
    - retry 2~3 次（exponential backoff）
    - 若仍失敗：回傳可辨識的錯誤，讓 router **fallback 到 VLM**
- 這樣可以把「OCR 不穩」從 **failed** 變成 **degraded but finished**（可交差的工程品質）

> 現在看到「PDF/圖片很多最後都走 vlm」：不是你路由壞掉，反而是 router 在救。
> 

---

### 6.2 `sample_table.pdf` 失敗，但 `text_ok.pdf` 成功

**原因**

- `text_ok.pdf`：text-based，可直接抽取 → Docling/pypdf 成功
- `sample_table.pdf`：掃描/不可抽取 → 需要 OCR；而OCR upstream 502 → 所以失敗

**結論**

- 是「測資類型」+「OCR upstream 不穩」疊加造成，不是docling 邏輯有問題。

---

### 6.3 GraphRAG endpoint Not Found / 422

- 先打 `/openapi.json` grep 出實際路由（避免猜路徑）
- 看到 `/v1/graphrag` 是 GET 且需要 query `keyword`
- 直接 `curl -i /v1/graphrag` 會 422（缺 keyword）是正常行為
- 用 `curl -G --data-urlencode` 送 keyword 後成功

---

### 6.4 Nginx rate limit：壓測時出現 429 / 503 / 504

- 429：你的 rate limit 生效（✅）
- 503/504：通常是 upstream timeout / queue 滿 / 或同時壓 VLM/OCR 造成延遲（⚠️）

已經看到「調整設定後 429 變多、503/504 變少」：這其實是好的結果

=> 代表 Gateway 把流量擋在外面，而不是把後端打爆。

---

## 7. Lineage（Chunk-level）

已經驗收到 lineage 具有 chunk-level 資訊（這段建議放結果截圖或 JSON 範例）：

- `job_id / filename / route / input_path`
- `chunk_count / qdrant_points / elapsed_sec / created_at`
- `chunks[]`
    - `chunk_id`
    - `qdrant_point_id`
    - `page`（若可得）
    - `text_len`
    - `preview`（截斷預覽）

---

## 8. 目前完成度對照

| 項目 | 狀態 | 你目前證據/驗收方式 |
| --- | --- | --- |
| Docling / OCR / VLM 封裝成非同步 API | ✅ | `/v1/jobs` + polling + `/result` |
| 動態路由切換（含 fallback） | ✅（OCR upstream 不穩但邏輯 OK） | 同一測資可 route_hint / auto；OCR 失敗會改走 VLM |
| Chunk + Embedding + Qdrant | ✅ | job 完成後 `qdrant_points > 0` |
| Neo4j（Doc/Chunk nodes + HAS_CHUNK） | ✅ | `MATCH (d:Document) RETURN count(d);` 等查詢 |
| GraphRAG demo endpoint | ✅ | `/v1/graphrag?keyword=...&limit=...` |
| Nginx Gateway（rate limit + upstream health） | ✅（需再調參） | 壓測出現 429，health/openapi 可透過 :8080 代理 |
| Lineage chunk-level | ✅ | `data/lineage/<job_id>.json` 有 chunks array |

---

## 9. 我學到什麼

- **「可抽取 PDF」與「掃描 PDF」是兩種世界**：前者 Docling/pypdf 就能做，後者一定要 OCR 或 VLM。
- **工程上不能相信 upstream 永遠正常**：要有 retry / timeout / fallback，不然系統很容易在 demo 當天翻車。
- **OpenAPI 是最可靠的真相來源**：遇到 Not Found 或參數錯，先看 `/openapi.json` 才不會瞎猜路徑。
- **Gateway 的目的不是跑更快**，是讓系統在流量/錯誤下「更可控」：429 比 504 更像工程。

---

## 10. 下一步（建議的優化順序）

1. **OCR 502 穩定化（retry + fallback）** ← 你已決定要做
2. OCR 品質評分（低品質 fallback VLM）
3. Gateway 調參（timeout、limit_req、proxy_next_upstream、健康檢查策略）
4. Lineage 再擴充（page info 更完整、chunk span、原始來源摘要）

---

# PR / Demo 建議

- Demo 時用兩份 PDF：
    - `text_ok.pdf`：展示 Docling 直接成功
    - `sample_table.pdf`：展示掃描 PDF → fallback OCR/VLM（並說明 OCR upstream 不穩，所以你已設計 retry + fallback）