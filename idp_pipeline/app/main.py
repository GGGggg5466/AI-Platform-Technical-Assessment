from fastapi import FastAPI
from app.routes import router

app = FastAPI(title="IDP Pipeline API", version="1.0.0")

@app.get("/health")
def health():
    return {"ok": True}

app.include_router(router, prefix="/v1")