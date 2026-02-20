import os
from typing import List, Dict, Any, Optional
from neo4j import GraphDatabase

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")

_driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

def close_driver():
    _driver.close()

def upsert_doc_and_chunks(
    job_id: str,
    filename: str,
    input_path: str,
    route: str,
    chunks: List[Dict[str, Any]],
) -> None:
    """
    Create:
      (:Document {job_id, filename, input_path, route})
      (:Chunk {job_id, chunk_id, text, qdrant_point_id?})
      (Document)-[:HAS_CHUNK]->(Chunk)
    """
    cypher = """
    MERGE (d:Document {job_id: $job_id})
    SET d.filename = $filename,
        d.input_path = $input_path,
        d.route = $route

    WITH d
    UNWIND $chunks AS c
      MERGE (ch:Chunk {job_id: $job_id, chunk_id: c.chunk_id})
      SET ch.text = c.text,
          ch.qdrant_point_id = c.qdrant_point_id
      MERGE (d)-[:HAS_CHUNK]->(ch)
    """

    with _driver.session(database=NEO4J_DATABASE) as session:
        session.run(
            cypher,
            job_id=job_id,
            filename=filename,
            input_path=input_path,
            route=route,
            chunks=chunks,
        )

def graph_find_chunks_by_keyword(keyword: str, limit: int = 5) -> List[Dict[str, Any]]:
    """
    Simple GraphRAG step 1: use graph to fetch related chunks by keyword.
    """
    cypher = """
    MATCH (d:Document)-[:HAS_CHUNK]->(ch:Chunk)
    WHERE toLower(ch.text) CONTAINS toLower($kw)
    RETURN d.job_id AS job_id, d.filename AS filename,
           ch.chunk_id AS chunk_id, ch.text AS text, ch.qdrant_point_id AS qdrant_point_id
    LIMIT $limit
    """
    with _driver.session(database=NEO4J_DATABASE) as session:
        res = session.run(cypher, kw=keyword, limit=limit)
        return [r.data() for r in res]

def graph_find_chunks_by_keyword(keyword: str, limit: int = 5) -> List[Dict[str, Any]]:
    """
    以 keyword 在 Chunk.text 做 contains 查詢，回傳 chunk 基本資訊
    """
    q = """
    MATCH (d:Document)-[:HAS_CHUNK]->(c:Chunk)
    WHERE toLower(c.text) CONTAINS toLower($keyword)
    RETURN d.filename AS filename, c.chunk_id AS chunk_id, c.text AS text, c.qdrant_point_id AS qdrant_point_id
    ORDER BY d.created_at DESC, c.chunk_id ASC
    LIMIT $limit
    """
    with _driver.session() as session:
        rows = session.run(q, keyword=keyword, limit=limit)
        return [dict(r) for r in rows]

def graph_fallback_top_chunks(limit: int = 5, filename: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    fallback：若 keyword 查不到，就抓「最新文件」或「指定 filename」的前 N 個 chunks
    """
    if filename:
        q = """
        MATCH (d:Document {filename: $filename})-[:HAS_CHUNK]->(c:Chunk)
        RETURN d.filename AS filename, c.chunk_id AS chunk_id, c.text AS text, c.qdrant_point_id AS qdrant_point_id
        ORDER BY c.chunk_id ASC
        LIMIT $limit
        """
        params = {"filename": filename, "limit": limit}
    else:
        q = """
        MATCH (d:Document)-[:HAS_CHUNK]->(c:Chunk)
        WITH d ORDER BY d.created_at DESC
        LIMIT 1
        MATCH (d)-[:HAS_CHUNK]->(c:Chunk)
        RETURN d.filename AS filename, c.chunk_id AS chunk_id, c.text AS text, c.qdrant_point_id AS qdrant_point_id
        ORDER BY c.chunk_id ASC
        LIMIT $limit
        """
        params = {"limit": limit}

    with _driver.session() as session:
        rows = session.run(q, **params)
        return [dict(r) for r in rows]