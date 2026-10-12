from fastapi import FastAPI
from . import sync
from .db import q

app = FastAPI(title="Megamind")

@app.get("/sync")
def status():
    return q("SELECT source, MAX(started) AS last_run, ok, count, error FROM sync_runs GROUP BY source")

@app.post("/sync")
@app.post("/sync/{source}")
def run(source: str | None = None):
    return sync.run(source)

@app.get("/items")
def items(source: str | None = None, search: str | None = None):
    return q("SELECT * FROM items WHERE (? IS NULL OR source=?) AND (? IS NULL OR title LIKE '%'||?||'%' OR description LIKE '%'||?||'%') ORDER BY date DESC",
             source, source, search, search, search)
