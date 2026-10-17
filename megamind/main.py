import base64, html, os, secrets, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse
from . import cluster, config, embed, llm, posts, social, sync
from .connectors.website import Website
from .connectors import CONNECTORS
from .db import COLS, connect, q, set_ignored

app = FastAPI(title="Megamind")
WEB = Path(__file__).resolve().parent.parent / "web"

@app.middleware("http")
async def password_gate(request: Request, call_next):
    # the app holds your data and will post for you, so it never serves without APP_PASSWORD (browser login popup, any username)
    if request.url.path in ("/static/logo.png", "/static/mark.png", "/static/favicon.png"):
        return await call_next(request)
    if request.url.path == "/linkedin/callback":  # LinkedIn sends you back here; the one-time `state` made by /linkedin/connect is the protection
        return await call_next(request)
    pw = os.environ.get("APP_PASSWORD")
    cron = os.environ.get("CRON_SECRET")  # Vercel Cron (and any scheduler you add) calls /cron/tick with this instead of the password
    if request.url.path == "/cron/tick" and request.query_params.get("key") and secrets.compare_digest(
            request.query_params["key"].encode(), config.cron_key().encode()):
        return await call_next(request)  # the private check link shown in Settings
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
    return {"fields": config.status(), "providers": {k: v[0] for k, v in llm.PRESETS.items()}}

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

def site_base(request):
    base = str(request.base_url)
    if base.startswith("http://") and "localhost" not in base and "127.0.0.1" not in base: base = "https://" + base[7:]  # behind Vercel's proxy
    return base

@app.get("/status")
def setup_status(request: Request):
    """What the Start-here checklist shows."""
    cfg = config.load()
    from . import mail, social
    base = site_base(request)
    one = lambda q_: q(q_)[0]
    with connect() as db:
        lt = db.execute("SELECT value FROM settings WHERE key='last_tick'").fetchone()
    return {"items": one("SELECT count(*) AS n FROM items WHERE ignored IS NOT TRUE")["n"], "projects": one("SELECT count(*) AS n FROM projects")["n"],
            "ai": llm.on(cfg), "email": mail.configured(cfg), "linkedin": social.linkedin_ready(cfg),
            "last_check": lt["value"] if lt else None, "daily_only_cron": True,
            "check_url": f"{base}cron/tick?key={config.cron_key()}",
            "scheduled": one("SELECT count(*) AS n FROM schedules WHERE active")["n"], "posted": one("SELECT count(*) AS n FROM posts WHERE status='published'")["n"],
            "linkedin_redirect": base + "linkedin/callback", "linkedin_expires": (q("SELECT value FROM settings WHERE key='li_expires'") or [{}])[0].get("value"),
            "awaiting": one("SELECT count(*) AS n FROM posts WHERE status IN ('draft','emailed','approved')")["n"],
            "next_run": one("SELECT min(next_run) AS t FROM schedules WHERE active")["t"], "last_posted": one("SELECT max(published_at) AS t FROM posts")["t"]}

@app.post("/test/{what}")
def test_connection(what: str):
    """One-click checks used by the Settings page. Every check is free (no post is made)."""
    cfg = config.load()
    from . import mail, social
    try:
        if what == "ai":
            return {"ok": True, "message": "The AI replied: " + llm.ask("Reply with the single word: ok", cfg).strip()[:60]}
        if what == "email":
            if not mail.configured(cfg): return {"ok": False, "message": "Fill in the Gmail address and app password first, and press Save."}
            mail.send(cfg, "Megamind test email", "If you can read this, Megamind can send you drafts. You can delete this email.")
            return {"ok": True, "message": f"Test email sent to {mail.owner(cfg)}. Check your inbox."}
        if what == "linkedin":
            import httpx
            r = httpx.get("https://api.linkedin.com/v2/userinfo", headers={"Authorization": "Bearer " + (cfg("LINKEDIN_ACCESS_TOKEN") or "")}, timeout=30)
            if r.status_code != 200: return {"ok": False, "message": f"LinkedIn said {r.status_code}. The token is missing, expired, or lacks the openid scope."}
            return {"ok": True, "message": "Connected as " + (r.json().get("name") or "your LinkedIn account")}
    except Exception as e:
        return {"ok": False, "message": str(e)[:300]}
    raise HTTPException(404)

@app.get("/static/{name}")
def static_file(name: str):  # only the three logo files; the gate lets these through without a password
    if name not in ("logo.png", "mark.png", "favicon.png"): raise HTTPException(404)
    return FileResponse(WEB / "static" / name, headers={"Cache-Control": "public, max-age=86400"})

@app.get("/sites")
def list_sites():
    env = Website.env_bases()
    return [{"url": u, "source": "vercel"} for u in env] + [{"url": u, "source": "page"} for u in config.sites() if u not in env]

@app.post("/sites")
def add_site(body: dict):
    try:
        return {"url": config.add_site(body.get("url"))}
    except ValueError as e:
        raise HTTPException(400, str(e))

@app.delete("/sites")
def remove_site(url: str):  # sites set in Vercel can only be removed there
    config.remove_site(url)
    return {"ok": True}

@app.get("/linkedin/connect")
def linkedin_connect(request: Request):
    cfg = config.load()
    if not (cfg("LINKEDIN_CLIENT_ID") and cfg("LINKEDIN_CLIENT_SECRET")):
        raise HTTPException(400, "Save your LinkedIn Client ID and Client Secret in Settings first.")
    state = secrets.token_urlsafe(24)
    with connect() as db:
        db.execute("INSERT INTO settings VALUES('li_state', %s) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (f"{state}|{time.time()}",))
    return RedirectResponse(social.auth_url(cfg("LINKEDIN_CLIENT_ID"), site_base(request) + "linkedin/callback", state))

@app.get("/linkedin/callback")
def linkedin_callback(request: Request, code: str = "", state: str = "", error: str = "", error_description: str = ""):
    def page(ok, msg):
        return HTMLResponse(f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>Megamind</title>"
                            f"<body style='font:16px system-ui;background:#060f23;color:#eaf1ff;max-width:520px;margin:12vh auto;padding:0 20px'>"
                            f"<h2>{'LinkedIn connected' if ok else 'LinkedIn did not connect'}</h2><p>{html.escape(msg)}</p>"
                            f"<p><a style='color:#27a2ff' href='/#settings'>Back to Megamind</a></p>", status_code=200 if ok else 400)
    if error:
        return page(False, f"LinkedIn said: {error_description or error}")
    with connect() as db:
        r = db.execute("SELECT value FROM settings WHERE key='li_state'").fetchone()
        saved, _, ts = (r["value"] if r else "").partition("|")
        if not (saved and state and secrets.compare_digest(saved.encode(), state.encode())) or time.time() - float(ts or 0) > 600:
            return page(False, "This sign-in link expired, or it wasn't started from Megamind. Go back and press Connect LinkedIn again.")
        db.execute("DELETE FROM settings WHERE key='li_state'")  # one use only
    try:
        token, secs = social.exchange(config.load(), code, site_base(request) + "linkedin/callback")
    except Exception as e:
        return page(False, str(e))
    config.save({"LINKEDIN_ACCESS_TOKEN": token})
    with connect() as db:
        exp = (datetime.now(timezone.utc) + timedelta(seconds=secs)).isoformat()
        db.execute("INSERT INTO settings VALUES('li_expires', %s) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (exp,))
        db.execute("DELETE FROM settings WHERE key='li_reminded'")
    return page(True, f"Done. Megamind can post to your LinkedIn for about {secs // 86400} days; it will email you before it expires.")
