import base64, os, secrets
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from . import cluster, embed, sync
from .connectors import CONNECTORS
from .db import COLS, connect, q, set_ignored

app = FastAPI(title="Megamind")
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
        return PlainTextResponse("Login required", 401, {"WWW-Authenticate": 'Basic realm="Megamind"'})
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
def group():
    if cluster.on():  # a model names and summarises the projects
        try:
            return cluster.run()
        except Exception as e:
            if not embed.on():
                raise HTTPException(502, f"Grouping model call failed: {e!r}")
    if embed.on():  # no model (or it failed): nearest-project matching, projects named after their first item
        with connect() as db:
            return embed.cluster(db)
    raise HTTPException(400, "Set OPENCODE_API_KEY + LLM_MODEL (or ANTHROPIC_API_KEY), or VOYAGE_API_KEY, in Vercel, redeploy, then try again.")

@app.get("/items")
def items(source: str | None = None, search: str | None = None, project: int | None = None, ignored: bool = False):
    p = {"s": source, "j": project, "ig": ignored, "q": search, "like": f"%{search}%" if search else None}
    where = "(%(s)s::text IS NULL OR source=%(s)s) AND (%(j)s::int IS NULL OR project_id=%(j)s) AND (ignored IS TRUE) = %(ig)s"
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
    return q("SELECT p.*, COUNT(i.id) AS item_count, MAX(i.date) AS last_activity FROM projects p LEFT JOIN items i ON i.project_id=p.id AND i.ignored IS NOT TRUE GROUP BY p.id ORDER BY p.id")

@app.patch("/items/{id}")
def edit_item(id: int, body: dict):  # {"project_id": 3 | null} and/or {"ignored": true | false}; clustering only fills empty project_ids, so moves stick
    with connect() as db:
        if "project_id" in body:
            db.execute("UPDATE items SET project_id=%s WHERE id=%s", (body["project_id"], id))
        if "ignored" in body:
            set_ignored(db, id, bool(body["ignored"]))
    return {"ok": True}

@app.patch("/projects/{id}")
def rename_project(id: int, body: dict):
    return q("UPDATE projects SET name=coalesce(%s,name), summary=coalesce(%s,summary) WHERE id=%s RETURNING *", (body.get("name"), body.get("summary"), id))
