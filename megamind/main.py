import os
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import FileResponse
from . import sync
from .connectors import CONNECTORS
from .db import q

app = FastAPI(title="ProjectPost")
WEB = Path(__file__).resolve().parent.parent / "web"

@app.get("/")
def home():
    return FileResponse(WEB / "index.html")

@app.get("/sync")
def status():
    last = {r["source"]: r for r in q("SELECT source, MAX(started) AS last_run, ok, count, error FROM sync_runs GROUP BY source")}
    off = os.environ.get("MEGAMIND_DISABLE", "").split(",")
    return [{"source": c.name, "configured": c.configured(), "disabled": c.name in off,
             **{k: last.get(c.name, {}).get(k) for k in ("last_run", "ok", "count", "error")}} for c in CONNECTORS]

@app.post("/sync")
@app.post("/sync/{source}")
def run(source: str | None = None):
    return sync.run(source)

@app.get("/items")
def items(source: str | None = None, search: str | None = None):
    return q("SELECT * FROM items WHERE (? IS NULL OR source=?) AND (? IS NULL OR title LIKE '%'||?||'%' OR description LIKE '%'||?||'%') ORDER BY date DESC",
             source, source, search, search, search)

@app.get("/projects")
def projects():
    return q("SELECT p.*, COUNT(i.id) AS item_count, MAX(i.date) AS last_activity FROM projects p LEFT JOIN items i ON i.project_id=p.id GROUP BY p.id")
