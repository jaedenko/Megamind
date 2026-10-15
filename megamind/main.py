import base64, os, secrets
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from . import cluster, sync
from .connectors import CONNECTORS
from .db import COLS, q

app = FastAPI(title="ProjectPost")
WEB = Path(__file__).resolve().parent.parent / "web"

@app.middleware("http")
async def password_gate(request: Request, call_next):
    # the app holds your data and will post for you, so it never serves without APP_PASSWORD (browser login popup, any username)
    pw = os.environ.get("APP_PASSWORD")
    if not pw:
        return PlainTextResponse("Set APP_PASSWORD in Vercel > Settings > Environment Variables, then redeploy.", 503)
    try:
        given = base64.b64decode(request.headers.get("authorization", "")[6:]).decode().partition(":")[2]
    except Exception:
        given = ""
    if not secrets.compare_digest(given.encode(), pw.encode()):
        return PlainTextResponse("Login required", 401, {"WWW-Authenticate": 'Basic realm="ProjectPost"'})
    return await call_next(request)

@app.get("/")
def home():
    return FileResponse(WEB / "index.html")

@app.get("/sync")
def status():
    last = {r["source"]: r for r in q("SELECT DISTINCT ON (source) source, started AS last_run, ok, count, error FROM sync_runs ORDER BY source, started DESC")}
    off = os.environ.get("MEGAMIND_DISABLE", "").split(",")
    return [{"source": c.name, "configured": c.configured(), "disabled": c.name in off,
             **{k: last.get(c.name, {}).get(k) for k in ("last_run", "ok", "count", "error")}} for c in CONNECTORS]

@app.post("/sync")
@app.post("/sync/{source}")
def run(source: str | None = None):
    return sync.run(source)

@app.get("/items")
def items(source: str | None = None, search: str | None = None, project_id: int | None = None):
    # ponytail: substring match is plenty at ~100s of items; the fts column is there when it isn't
    return q(f"SELECT {COLS} FROM items WHERE (%(s)s::text IS NULL OR source=%(s)s) AND (%(j)s::int IS NULL OR project_id=%(j)s) AND (%(p)s::text IS NULL OR title ILIKE %(p)s OR description ILIKE %(p)s) ORDER BY date DESC NULLS LAST LIMIT 500",
             {"s": source, "j": project_id, "p": f"%{search}%" if search else None})

@app.get("/projects")
def projects():
    return q("SELECT p.*, COUNT(i.id) AS item_count, MAX(i.date) AS last_activity FROM projects p LEFT JOIN items i ON i.project_id=p.id GROUP BY p.id")

@app.post("/cluster")
def group():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return JSONResponse({"error": "Set ANTHROPIC_API_KEY in Vercel > Settings > Environment Variables, then redeploy."}, 503)
    return cluster.run()

@app.patch("/items/{id}")
def move(id: int, body: dict):  # {"project_id": 3 | null}; a manual move is never overwritten by clustering (it only fills NULLs)
    return q("UPDATE items SET project_id=%s WHERE id=%s RETURNING id, project_id", (body.get("project_id"), id))

@app.patch("/projects/{id}")
def rename(id: int, body: dict):
    return q("UPDATE projects SET name=coalesce(%s,name), summary=coalesce(%s,summary) WHERE id=%s RETURNING *", (body.get("name"), body.get("summary"), id))
