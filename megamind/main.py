import base64, os, secrets
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from . import cluster, config, embed, llm, posts, sync
from .connectors import CONNECTORS
from .db import COLS, connect, q, set_ignored

app = FastAPI(title="Megamind")
WEB = Path(__file__).resolve().parent.parent / "web"

@app.middleware("http")
async def password_gate(request: Request, call_next):
    # the app holds your data and will post for you, so it never serves without APP_PASSWORD (browser login popup, any username)
    pw = os.environ.get("APP_PASSWORD")
    cron = os.environ.get("CRON_SECRET")  # Vercel Cron (and any scheduler you add) calls /cron/tick with this instead of the password
    if cron and request.url.path == "/cron/tick" and secrets.compare_digest(request.headers.get("authorization", "").encode(), f"Bearer {cron}".encode()):
        return await call_next(request)
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
    if llm.on():  # a model names and summarises the projects
        try:
            return cluster.run()
        except Exception as e:
            if not embed.on():
                raise HTTPException(502, f"Grouping model call failed: {e!r}")
    if embed.on():  # no model (or it failed): nearest-project matching, projects named after their first item
        with connect() as db:
            return embed.cluster(db)
    raise HTTPException(400, "Add an AI provider key in Settings (or a VOYAGE_API_KEY in Vercel), then try again.")

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

@app.get("/settings")
def get_settings():
    return {"fields": config.status(), "providers": list(llm.PRESETS)}

@app.put("/settings")
def put_settings(body: dict):  # {"LLM_API_KEY": "sk-..."} sets; {"LLM_API_KEY": null} clears; "" leaves as is
    try:
        config.save(body)
    except KeyError as e:
        raise HTTPException(400, f"Unknown setting {e}")
    return get_settings()

@app.get("/posts")
def list_posts():  # never returns the reply token
    return q("SELECT p.id, p.project_id, pr.name AS project, p.angle, p.body, p.x_body, p.status, p.revisions, p.result, p.error, p.created, p.published_at "
             "FROM posts p LEFT JOIN projects pr ON pr.id=p.project_id ORDER BY p.id DESC LIMIT 50")

@app.post("/posts")
def draft_now(body: dict):  # {"project_id": 3 | null (auto), "angle": ""}: writes a draft and emails it right now
    with connect() as db:
        db.autocommit = True
        try:
            return {"id": posts.draft(db, config.load(), body.get("project_id"), body.get("angle") or "")}
        except Exception as e:
            raise HTTPException(400, f"Could not draft: {e}")

@app.post("/posts/{id}/discard")
def discard(id: int):
    return q("UPDATE posts SET status='rejected' WHERE id=%s AND status IN ('draft','emailed','approved') RETURNING id", (id,))

@app.get("/schedules")
def list_schedules():
    return q("SELECT s.*, pr.name AS project FROM schedules s LEFT JOIN projects pr ON pr.id=s.project_id WHERE s.active ORDER BY s.next_run")

@app.post("/schedules")
def add_schedule(body: dict):  # {"project_id": null (auto), "angle": "", "run_at": "2026-10-20T14:00:00Z", "every_days": 7 | null}
    return q("INSERT INTO schedules(project_id, angle, next_run, every_days) VALUES(%s,%s,%s,%s) RETURNING id",
             (body.get("project_id"), body.get("angle") or "", body["run_at"], body.get("every_days") or None))

@app.delete("/schedules/{id}")
def del_schedule(id: int):
    return q("UPDATE schedules SET active=false WHERE id=%s RETURNING id", (id,))

@app.get("/cron/tick")
@app.post("/cron/tick")
def tick():  # Vercel Cron GETs this; the "Check now" button POSTs it
    return posts.tick()
