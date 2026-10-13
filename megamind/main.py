import base64, os, secrets
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from . import embed, sync
from .connectors import CONNECTORS
from .db import COLS, connect, q

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

@app.post("/cluster")
def cluster():
    if not embed.on():
        raise HTTPException(400, "Needs VOYAGE_API_KEY and pgvector, then a sync to embed items.")
    with connect() as db:
        return embed.cluster(db)

@app.get("/items")
def items(source: str | None = None, search: str | None = None, project: int | None = None):
    p = {"s": source, "j": project, "q": search, "like": f"%{search}%" if search else None}
    where = "(%(s)s::text IS NULL OR source=%(s)s) AND (%(j)s::int IS NULL OR project_id=%(j)s)"
    if search and embed.on():
        try:  # meaning-based order; exact keyword hits float to the top, un-embedded items sink
            p["v"] = str(embed.embed([search], "query")[0])
            return q(f"SELECT {COLS} FROM items WHERE {where} ORDER BY (fts @@ websearch_to_tsquery('english', %(q)s)) DESC, "
                     "embedding <=> %(v)s::vector NULLS LAST LIMIT 50", p)
        except Exception:
            pass  # Voyage down: fall back to keyword
    kw = " AND (%(q)s::text IS NULL OR fts @@ websearch_to_tsquery('english', %(q)s) OR title ILIKE %(like)s OR description ILIKE %(like)s)"
    return q(f"SELECT {COLS} FROM items WHERE {where}{kw} ORDER BY ts_rank(fts, websearch_to_tsquery('english', coalesce(%(q)s,''))) DESC, date DESC NULLS LAST LIMIT 500", p)

@app.get("/projects")
def projects():
    return q("SELECT p.*, COUNT(i.id) AS item_count, MAX(i.date) AS last_activity FROM projects p LEFT JOIN items i ON i.project_id=p.id GROUP BY p.id")
