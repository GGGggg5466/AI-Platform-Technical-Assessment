# 02 — IDP Pipeline (Docling / OCR / VLM) 非同步 API

> 目標：把 PDF/圖片等非結構資料，轉成可用的 **Markdown/JSON**，並自動切片（chunks）寫入 **向量資料庫 Qdrant**，同時把處理過程（lineage）記錄成 JSON，並（可選）同步寫入 Neo4j 供 GraphRAG 使用。
> 

---

## 1. 目前完成度總覽（對照老師要求）

### ✅ 1) 流水線邏輯與動態路由（Docling / OCR / VLM）

**狀態：部分完成（已可運作，且有 page-level 記錄）**

- 目前的 `route="docling"` 走的是「**逐頁**」策略：
    
    先抽每頁文字 → 若文字不足就轉圖片做 OCR → OCR 品質差或像表格再升級用 VLM。
    jobs
    
- 每頁會記錄 `used_route`（docling/ocr/vlm）與 `ocr_score`（若有 OCR）到 `pages_meta`，並產生 `page_info_override`。jobs

---

### ✅ 2) 向量庫 + 圖譜整合（Qdrant + Neo4j + GraphRAG）

**狀態：Qdrant ✅、Neo4j/GraphRAG ✅（要先啟動服務）**

- Qdrant：
    - `ensure_collection()` 會自動建立 collection。vstore_qdrant
    - `upsert_chunks()` 支援 `per_chunk_meta`，可以把 `page / used_route / ocr_score / image` 合併進每個 point payload。vstore_qdrant
- Jobs 在寫入 Qdrant 前，會先組好 `per_chunk_meta`（從 `page_info_override.pages` 對應回 chunk 的頁碼），再呼叫 `upsert_chunks(..., per_chunk_meta=...)`。jobs

---

### ✅ 3) API + 容器化 + Gateway（Docker / Nginx）

**狀態：可用（已驗證能 POST job、GET status）**

- `/v1/jobs`：上傳檔案建立 job（multipart/form-data）
- `/v1/jobs/{job_id}`：查詢 job 狀態（running / finished / failed）

---

### ✅ 4) 資料血緣（Lineage）

**狀態：已完成（chunk-level + 連到 qdrant point id）**

Lineage JSON 會包含：

- `job_id / filename / route / input_path`
- `chunk_count / qdrant_points / elapsed_sec / created_at`
- `chunks[]`（每個 chunk 的 `page / start / end / qdrant_point_id / preview` 等）b97bc49832fa4de2b8be46deb537241c

範例（這次成功的 `sample_table.pdf`）顯示：

- `route: docling`
- `chunk_count: 4`
- `qdrant_points: 4`b97bc49832fa4de2b8be46deb537241c

---

## 2. 系統架構）

**Client → FastAPI → Jobs（非同步） → Pipeline（Docling/OCR/VLM） → Chunking → Embedding → Qdrant + Neo4j → Lineage JSON**

關鍵點：

- **Job 狀態**在處理中為 `running`，成功後 `finished`，錯誤則 `failed`（error 會寫進 job）。jobs
- Pipeline 會把每個 chunk upsert 到 Qdrant，並把 `qdrant_point_id` 回填到 lineage。jobs

---

## 3. 如何啟動（建議順序）

### 3.1 啟動依賴服務（Qdrant / Neo4j / Gateway）

```
docker compose up-d qdrant neo4j gateway
docker composeps
```

確認：

- Qdrant：本機 `6333` 有開（或 gateway 內部可連）
- Neo4j：Bolt `7687`、Browser `7474` 有開

---

### 3.2 啟動 API（FastAPI）

在 `idp_pipeline/` 目錄：

```
uvicorn app.main:app--host0.0.0.0--port8000--reload
```

Health check：

```
curl-s http://127.0.0.1:8000/health
```

---

## 4. 如何測試
### 4.1 建立 Job（上傳 sample_table.pdf）

```
curl-s-X POST"http://127.0.0.1:8000/v1/jobs" \
-F"file=@data/uploads/pdf/sample_table.pdf" \
-F"route=docling"
```

回應會像：

```
{"job_id":"<YOUR_JOB_ID>"}
```

---

### 4.2 查 Job 狀態

```
curl-s"http://127.0.0.1:8000/v1/jobs/<YOUR_JOB_ID>"
```

- `running`：代表還在抽取 / OCR / VLM / embedding / upsert（任何一步慢都會卡在 running）
- `finished`：代表 chunks 已產生、Qdrant 已寫入、lineage 已寫出jobs
- `failed`：看 `error` 欄位（通常是依賴服務沒起來、或 OCR/VLM upstream 問題）

---

## 5. 結果輸出在哪裡、怎麼看

### 5.1 Lineage JSON（最重要的可交作業證據）

成功後 lineage 會包含 chunk-level 資訊與 qdrant point id。
b97bc49832fa4de2b8be46deb537241c

---

### 5.2 Qdrant
`upsert_chunks()` 會把 `meta` 與 `per_chunk_meta` 合併到 payload。
vstore_qdrant

---

## 6. 已知限制

1. **PDF 表格/複雜圖表的“結構化表格輸出”仍有限**
    - 目前產出以文字/Markdown 為主，表格若 OCR/VLM 抽得不好，仍可能變成「排版不完美」的文字塊。
2. **輸出 JSON 目前以 chunk-level 為主，page-level 的 used_route/ocr_score 已建立但未完全展示在最終 JSON**
    - 不過 `pages_meta.used_route / ocr_score` 的資料已在 `jobs.py` 產生。jobs
3. **依賴服務未啟動會導致 job failed**
    - Qdrant / Neo4j 任一未就緒，後段寫入會失敗（屬於部署/啟動順序問題）。