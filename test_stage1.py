# Needs a throwaway Postgres: DATABASE_URL=postgresql://... python test_stage1.py  (tests wipe the public schema!)
import base64, os
os.environ["APP_PASSWORD"] = "pw"
from megamind import sync, db
from megamind.connectors.website import extract, _rows

with db.connect() as _d:
    _d.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public"); _d.commit()
db._ready = False

def test_extract_skips_chrome():
    t, text, img = extract('<title>Gripper</title><meta property="og:image" content="x.png"><nav>menu</nav><p>Built a gripper</p><script>x()</script>')
    assert (t, text, img) == ("Gripper", "Built a gripper", "x.png")

def test_rows_sitemap_and_rss():
    sm = b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>http://a/1</loc><lastmod>2026-01-01</lastmod></url></urlset>'
    assert list(_rows(sm)) == [("http://a/1", "2026-01-01")]
    assert list(_rows(b"<rss><channel><item><link>http://a/2</link></item></channel></rss>")) == [("http://a/2", None)]

def test_sync_upsert_keeps_manual_edits():
    class Fake:
        name, v = "fake", ["one"]
        def configured(s): return True
        def fetch(s): yield {"ext_id": "1", "title": s.v[0], "date": "2026-01-01", "tags": ["a"], "metadata": {"k": 1}}
    f = Fake(); sync.CONNECTORS[:] = [f]
    assert sync.run() == {"fake": 1}
    d = db.connect(); d.execute("UPDATE items SET edited=true, title='mine'"); d.commit(); d.close()
    f.v[0] = "two"; sync.run()
    rows = db.q("SELECT title, tags, metadata FROM items")
    assert rows == [{"title": "mine", "tags": ["a"], "metadata": {"k": 1}}]  # edited item untouched, no duplicate row

def test_failing_source_is_recorded_and_others_survive():
    class Bad:
        name = "bad"
        def configured(s): return True
        def fetch(s): yield {"ext_id": "1", "title": "ok"}; raise RuntimeError("boom")
    sync.CONNECTORS[:] = [Bad()]
    assert "boom" in sync.run()["bad"]
    assert db.q("SELECT count(*) AS n FROM items WHERE source='bad'")[0]["n"] == 1
    assert db.q("SELECT ok FROM sync_runs WHERE source='bad'") == [{"ok": False}]

def test_notion_reads_nested_text():
    from megamind.connectors import notion
    notion.time.sleep = lambda s: None
    rt = lambda x: {"rich_text": [{"plain_text": x}]}
    tree = {"top": [{"id": "tog", "type": "toggle", "toggle": rt("outer"), "has_children": True},
                    {"id": "cp", "type": "child_page", "child_page": {}, "has_children": True}],
            "tog": [{"id": "x", "type": "paragraph", "paragraph": rt("inner"), "has_children": False}]}
    class R:
        def __init__(s, d): s.d = d
        def raise_for_status(s): return s
        def json(s): return {"results": s.d, "has_more": False}
    class C:
        def get(s, url, params): return R(tree[url.split("/")[2]])
    assert notion.Notion()._blocks(C(), "top") == ["outer", "inner"]

def test_notion_page_records_parent_and_database_name():
    from megamind.connectors import notion
    n = notion.Notion(); n._blocks = lambda c, id: []
    class C:
        def get(s, url):
            class R:
                status_code = 200
                def json(r): return {"title": [{"plain_text": "Tasks"}]}
            return R()
    page = lambda par: {"id": "p", "url": "u", "last_edited_time": "2026-01-01", "properties": {"Name": {"type": "title", "title": [{"plain_text": "Fix bug"}]}}, "parent": par}
    assert n._page(C(), page({"type": "database_id", "database_id": "db1"}))["metadata"] == {"parent": "db1", "parent_title": "Tasks"}
    assert n._page(C(), page({"type": "page_id", "page_id": "pg"}))["metadata"] == {"parent": "pg", "parent_title": None}
    assert n._page(C(), page({"type": "workspace", "workspace": True}))["metadata"] == {"parent": None, "parent_title": None}

def test_schema_has_generic_columns_and_fulltext():
    names = {r["table_name"] for r in db.q("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")}
    assert {"items", "projects", "posts", "corrections", "settings", "sync_runs"} <= names
    assert db.q("SELECT id FROM items WHERE fts @@ websearch_to_tsquery('english','mine')")  # title of the edited item above

def test_cluster_parse_trusts_only_ids_we_sent():
    from megamind.cluster import parse
    reply = 'sure! {"projects":[{"id":9,"name":"Gripper","summary":"s","items":[1,2,99,"x",[3]]},{"id":null,"name":"Ghost","items":[99]},{"id":2,"name":"Old","items":[3]}]}'
    # 99 wasn't sent, id 9 isn't a known project (-> new), Ghost has no valid items (dropped), id 2 is known (reused)
    assert parse(reply, {1, 2, 3}, {2}) == [(None, "Gripper", "s", [1, 2]), (2, "Old", "", [3])]

def test_llm_works_with_any_provider_key():
    from megamind import config, llm
    seen = []
    class R:
        def __init__(s, j): s.j = j
        def raise_for_status(s): return s
        def json(s): return s.j
    llm.httpx.post = lambda url, **kw: (seen.append((url, kw)), R({"choices": [{"message": {"content": "hi"}}], "content": [{"text": "claude"}]}))[1]
    for k in ("OPENCODE_API_KEY", "ANTHROPIC_API_KEY", "LLM_API_KEY", "LLM_MODEL", "LLM_PROVIDER"): os.environ.pop(k, None)
    assert not llm.on()
    config.save({"LLM_PROVIDER": "openrouter", "LLM_API_KEY": "k1", "LLM_MODEL": "m1"})  # what the Settings box does
    assert llm.on() and llm.ask("x") == "hi"
    url, kw = seen[-1]
    assert url == "https://openrouter.ai/api/v1/chat/completions" and kw["headers"]["Authorization"] == "Bearer k1" and kw["json"]["model"] == "m1"
    config.save({"LLM_PROVIDER": "custom", "LLM_BASE_URL": "https://my.host/v1/"}); llm.ask("x")
    assert seen[-1][0] == "https://my.host/v1/chat/completions"  # any OpenAI-compatible server
    config.save({"LLM_PROVIDER": "anthropic", "LLM_BASE_URL": None}); assert llm.ask("x") == "claude"
    assert seen[-1][0] == "https://api.anthropic.com/v1/messages" and seen[-1][1]["headers"]["x-api-key"] == "k1"
    config.save({"LLM_PROVIDER": "nonsense"})
    try: llm.ask("x"); assert False
    except RuntimeError as e: assert "Unknown LLM_PROVIDER" in str(e)
    config.save({k: None for k in ("LLM_PROVIDER", "LLM_API_KEY", "LLM_MODEL", "LLM_BASE_URL")})
    os.environ["OPENCODE_API_KEY"] = "ock"; os.environ["LLM_MODEL"] = "z"  # older env names keep working
    llm.ask("x"); assert seen[-1][0] == "https://opencode.ai/zen/v1/chat/completions"
    for k in ("OPENCODE_API_KEY", "LLM_MODEL"): os.environ.pop(k, None)

def test_config_page_beats_env_and_secrets_stay_hidden():
    from megamind import config
    os.environ["LINKEDIN_ACCESS_TOKEN"] = "from-env"
    assert config.load()("LINKEDIN_ACCESS_TOKEN") == "from-env" and config.status()["LINKEDIN_ACCESS_TOKEN"] == {"secret": True, "source": "env", "set": True, "value": None}
    config.save({"LINKEDIN_ACCESS_TOKEN": "from-page", "MAIL_TO": "me@x.com", "LLM_MODEL": ""})  # "" = untouched form field
    assert config.load()("LINKEDIN_ACCESS_TOKEN") == "from-page" and config.status()["MAIL_TO"]["value"] == "me@x.com"
    assert "from-page" not in str(config.status()) and config.status()["LLM_MODEL"]["set"] is False
    try: config.save({"NOT_A_SETTING": "x"}); assert False
    except KeyError: pass
    config.save({"LINKEDIN_ACCESS_TOKEN": None, "MAIL_TO": None}); del os.environ["LINKEDIN_ACCESS_TOKEN"]
    assert config.load()("LINKEDIN_ACCESS_TOKEN") is None

def test_ignore_rules_cascade_buttons_and_restore():
    from fastapi.testclient import TestClient
    from megamind.main import app
    c, ok = TestClient(app), {"Authorization": "Basic " + base64.b64encode(b"me:pw").decode()}
    def add(d, ext, title, **md):
        d.execute("INSERT INTO items(source,ext_id,title,metadata) VALUES('notion',%s,%s,%s)", (ext, title, db.Jsonb(md)))
    with db.connect() as d:
        for a in [("T", "tasks"), ("kid", "Do laundry", {"parent": "T"}), ("row", "Fix bug", {"parent_title": "Tasks"}),
                  ("C", "College Packing List"), ("R", "Robot")]:
            add(d, *a[:2], **(a[2] if len(a) > 2 else {}))
        db.apply_ignore(d)
    ign = lambda: {r["ext_id"]: r["ignored"] for r in db.q("SELECT ext_id, ignored FROM items WHERE source='notion'")}
    assert ign() == {"T": True, "kid": True, "row": True, "C": True, "R": False}  # titles case-insensitive; nested + database rows follow
    shown = lambda q="": [i["title"] for i in c.get("/items" + q, headers=ok).json() if i["title"] in {"tasks", "Do laundry", "Fix bug", "College Packing List", "Robot"}]
    assert shown() == ["Robot"] and len(shown("?ignored=true")) == 4
    tid = next(i["id"] for i in c.get("/items?ignored=true", headers=ok).json() if i["title"] == "tasks")
    c.patch(f"/items/{tid}", json={"ignored": False}, headers=ok)  # restoring a page restores what's nested under it
    assert ign()["T"] is False and ign()["kid"] is False and ign()["row"] is True
    rid = next(i["id"] for i in c.get("/items", headers=ok).json() if i["title"] == "Robot")
    c.patch(f"/items/{rid}", json={"ignored": True}, headers=ok)
    with db.connect() as d: db.apply_ignore(d)  # a later sync must not undo your clicks
    assert ign()["T"] is False and ign()["R"] is True
    pid = db.q("INSERT INTO projects(name) VALUES('X') RETURNING id")[0]["id"]
    c.patch(f"/items/{rid}", json={"project_id": pid}, headers=ok)
    assert c.get("/projects", headers=ok).json()[0]["item_count"] == 0  # ignored items don't count
    from megamind import cluster
    with db.connect() as d: d.execute("UPDATE items SET project_id=NULL"); d.commit()
    every = [r["id"] for r in db.q("SELECT id FROM items WHERE source='notion'")]
    cluster.ask = lambda prompt: '{"projects":[{"id":null,"name":"All","items":%s}]}' % every  # model tries to grab ignored items too
    assert cluster.run() == {"projects_created": 1, "items_placed": 2}  # only the 2 restored ones (tasks + Do laundry)
    with db.connect() as d:
        d.execute("DELETE FROM items WHERE source='notion'; DELETE FROM projects")  # later tests expect a clean slate

def test_cluster_fills_only_unassigned():
    from megamind import cluster
    d = db.connect(); mine = d.execute("INSERT INTO projects(name) VALUES('Mine') RETURNING id").fetchone()["id"]
    ids = [d.execute("INSERT INTO items(source,ext_id,title,project_id) VALUES('t',%s,%s,%s) RETURNING id", (n, n, mine if n == "c" else None)).fetchone()["id"] for n in "abc"]
    d.commit(); d.close()
    cluster.ask = lambda prompt: '{"projects":[{"id":null,"name":"New","summary":"","items":%s}]}' % ids  # also tries to grab the manual one
    assert cluster.run() == {"projects_created": 1, "items_placed": 2}
    got = [r["project_id"] for r in db.q("SELECT project_id FROM items WHERE source='t' ORDER BY id")]
    assert got[0] == got[1] != mine and got[2] == mine  # manual assignment survives
    assert cluster.run() == {"projects_created": 0, "items_placed": 0}  # nothing unassigned -> no model call
    d = db.connect(); d.execute("DELETE FROM items WHERE source='t'"); d.execute("DELETE FROM projects"); d.commit(); d.close()  # later tests expect none

def test_api_gate_and_endpoints():
    from fastapi.testclient import TestClient
    from megamind.main import app
    from megamind.connectors import CONNECTORS, GitHub, Notion, Website
    CONNECTORS[:] = [GitHub(), Notion(), Website()]  # same list object sync uses; earlier tests swapped in fakes
    c = TestClient(app)
    assert c.get("/sync").status_code == 401
    ok = {"Authorization": "Basic " + base64.b64encode(b"me:pw").decode()}
    assert c.get("/sync", headers={"Authorization": "Basic " + base64.b64encode(b"me:wrong").decode()}).status_code == 401
    assert {r["source"] for r in c.get("/sync", headers=ok).json()} == {"github", "notion", "website"}
    assert c.get("/", headers=ok).status_code == 200 and c.get("/projects", headers=ok).json() == []
    assert any(i["title"] == "mine" for i in c.get("/items?search=MIN", headers=ok).json())
    del os.environ["APP_PASSWORD"]
    assert c.get("/", headers=ok).status_code == 503  # fails closed
    os.environ["APP_PASSWORD"] = "pw"

def test_search_keyword_fallback_and_project_filter():
    from fastapi.testclient import TestClient
    from megamind.main import app
    c, ok = TestClient(app), {"Authorization": "Basic " + base64.b64encode(b"me:pw").decode()}
    d = db.connect(); d.execute("INSERT INTO projects(name) VALUES('P')"); d.execute("UPDATE items SET project_id=(SELECT id FROM projects)"); d.commit(); d.close()
    pid = c.get("/projects", headers=ok).json()[0]["id"]
    assert len(c.get(f"/items?project={pid}", headers=ok).json()) == 2
    assert c.get(f"/items?project={pid + 1}", headers=ok).json() == []
    assert c.get("/items?search=zzzz", headers=ok).json() == []
    assert c.post("/cluster", headers=ok).status_code == 400  # no VOYAGE_API_KEY

def test_embed_and_cluster():  # needs pgvector (Neon has it); skipped on a plain local Postgres
    if not db.VECTOR: return print("skip: no pgvector")
    from megamind import embed
    vec = lambda *hot: [1.0 if i in hot else 0.0 for i in range(1024)]
    vs = {"alpha robot": vec(0), "alpha arm": vec(0, 1), "tax notes": vec(5)}
    embed.embed = lambda texts, kind="document": [vs[t.split("\n")[0]] for t in texts]
    os.environ["VOYAGE_API_KEY"] = "x"
    with db.connect() as d:
        d.execute("DELETE FROM items; DELETE FROM projects")
        for i, t in enumerate(vs): d.execute("INSERT INTO items(source,ext_id,title) VALUES('t',%s,%s)", (str(i), t))
        assert embed.pending(d) == 3 and embed.pending(d) == 0
        assert embed.cluster(d) == {"items_placed": 3, "projects_created": 2}
        assert d.execute("SELECT count(DISTINCT project_id) AS n FROM items WHERE title LIKE 'alpha%%'").fetchone()["n"] == 1
    del os.environ["VOYAGE_API_KEY"]

def test_linkedin_request_has_the_right_shape():
    from megamind import social
    seen = []
    class R:
        status_code, text = 201, ""
        def __init__(s, j=None, h=None): s.j, s.headers = j or {}, h or {}
        def json(s): return s.j
    social.httpx.post = lambda url, **kw: (seen.append(("POST", url, kw)), R({}, {"x-restli-id": "urn:li:share:7"}))[1]
    social.httpx.get = lambda url, **kw: (seen.append(("GET", url, kw)), R({"sub": "abc123"}))[1]
    cfg = {"LINKEDIN_ACCESS_TOKEN": "tok"}.get
    assert social.post_linkedin(cfg, "a_b #1 (x)") == "https://www.linkedin.com/feed/update/urn:li:share:7/"
    _, url, kw = seen[-1]
    assert url == "https://api.linkedin.com/rest/posts" and kw["json"]["author"] == "urn:li:person:abc123" and kw["json"]["commentary"] == "a\\_b \\#1 \\(x\\)"
    assert kw["headers"]["Authorization"] == "Bearer tok" and kw["headers"]["X-Restli-Protocol-Version"] == "2.0.0" and kw["headers"]["LinkedIn-Version"]
    assert not hasattr(social, "post_x")  # Megamind never posts to X

def test_reply_text_and_approval_are_strict():
    from megamind.mail import reply_text
    from megamind.posts import classify
    assert reply_text("Y\n\nOn Thu, Oct 15, 2026 at 3:04 PM Me <me@x.com>\nwrote:\n> quoted\n> more") == "Y"
    assert reply_text("make it shorter\n> old draft\n\n-----Original Message-----\nold") == "make it shorter"
    assert [classify(t) for t in ("Y", " yes! ", "👍", "👍🏽", "LGTM", "n", "No.", "👎")] == ["approve"] * 5 + ["no"] * 3
    assert [classify(t) for t in ("yes but shorten it", "👍 love it", "y\nand add the numbers", "ok so what about X", "")] == ["feedback"] * 5  # only a bare yes posts

def test_schedule_advances_past_now():
    from datetime import datetime, timedelta, timezone
    from megamind.posts import _advance
    now = datetime.now(timezone.utc)
    nxt = _advance({"next_run": now - timedelta(days=8), "every_days": 7}, now)
    assert now < nxt <= now + timedelta(days=7)

def test_mail_send_and_poll_speak_smtp_and_imap():
    from email.message import EmailMessage
    from megamind import mail
    sent = []
    class SMTP:
        def __init__(s, host, port, timeout): sent.append(("connect", host, port))
        def __enter__(s): return s
        def __exit__(s, *a): pass
        def login(s, u, p): sent.append(("login", u, p))
        def send_message(s, m): sent.append(("msg", m))
    mail.smtplib.SMTP_SSL = SMTP
    cfg = {"MAIL_USER": "bot@gmail.com", "MAIL_PASSWORD": "app pw", "MAIL_TO": "me@x.com"}.get
    mid = mail.send(cfg, "[Megamind post-1-ab12] hi", "body", "<prev@t>")
    m = sent[-1][1]
    assert sent[0] == ("connect", "smtp.gmail.com", 465) and sent[1] == ("login", "bot@gmail.com", "app pw")
    assert m["To"] == "me@x.com" and m["Message-ID"] == mid and m["In-Reply-To"] == "<prev@t>" and m["Subject"].startswith("[Megamind post-1-ab12]")
    raw = EmailMessage(); raw["From"] = "Me <ME@x.com>"; raw["Subject"] = "Re: [Megamind post-1-ab12] hi"; raw["Message-ID"] = "<r1@t>"; raw["In-Reply-To"] = mid
    raw.set_content("Y\n"); raw.add_alternative("<p>Y</p>", subtype="html")
    log = []
    class IMAP:
        def __init__(s, host, port, timeout): log.append(("connect", host, port))
        def __enter__(s): return s
        def __exit__(s, *a): pass
        def login(s, u, p): pass
        def select(s, box): log.append(("select", box))
        def search(s, *a): log.append(("search",) + a); return "OK", [b"7"]
        def fetch(s, num, spec): log.append(("fetch", num, spec)); return "OK", [(b"7 (BODY[] {1}", raw.as_bytes()), b")"]
        def store(s, num, op, flag): log.append(("store", num, flag))
    mail.imaplib.IMAP4_SSL = IMAP
    got = []
    mail.poll(cfg, lambda msg: got.append(msg) or True)
    assert got[0]["from"] == "me@x.com" and got[0]["text"].strip() == "Y" and got[0]["refs"].strip().split()[0] == mid and mail.TAG.search(got[0]["subject"])
    assert ("search", None, "UNSEEN", "SUBJECT", '"[Megamind post-"') in log and ("fetch", b"7", "(BODY.PEEK[])") in log and ("store", b"7", "\\Seen") in log
    log.clear(); mail.poll(cfg, lambda msg: False); assert not any(x[0] == "store" for x in log)  # unhandled stays unread

def test_full_email_approval_loop():
    import re
    from megamind import config, llm, mail, posts, social
    with db.connect() as d:
        d.execute("DELETE FROM posts; DELETE FROM schedules; DELETE FROM items; DELETE FROM projects")
        pid = d.execute("INSERT INTO projects(name) VALUES('Gripper') RETURNING id").fetchone()["id"]
        for i, t in enumerate(("Built a gripper", "Tested it")): d.execute("INSERT INTO items(source,ext_id,title,project_id) VALUES('t',%s,%s,%s)", (str(i), t, pid))
    for k, v in {"MAIL_USER": "me@x.com", "MAIL_PASSWORD": "pw", "LINKEDIN_ACCESS_TOKEN": "5"}.items(): os.environ[k] = v
    state = {"x_len": 20}
    def fake_llm(prompt, cfg=None):
        if "THE OWNER'S REPLY" in prompt:
            fb = prompt.split("<<<\n")[1].split("\n>>>")[0]
            return '{"linkedin": "%s", "x": "x v2"}' % ("Owner's own article" if "MY ARTICLE" in fb else "LI revised")
        return '{"linkedin": "LI v1", "x": "%s"}' % ("x" * state["x_len"])
    llm.ask = fake_llm
    sent, inbox, calls = [], [], []
    mail.send = lambda cfg, subject, body, in_reply_to=None: (sent.append((subject, body)), f"<m{len(sent)}@t>")[1]
    def fake_poll(cfg, handle):
        for m in list(inbox):
            if handle(m): inbox.remove(m)
    mail.poll = fake_poll
    social.post_linkedin = lambda cfg, text: (calls.append(("li", text)), "https://li.com/1")[1]
    row = lambda: db.q("SELECT * FROM posts ORDER BY id DESC LIMIT 1")[0]
    def reply(text, frm="me@x.com", token=None):
        tag = re.search(r"\[Megamind post-\d+-[a-z0-9]+\]", sent[-1][0])[0]
        if token: tag = re.sub(r"-[a-z0-9]+\]", f"-{token}]", tag)
        inbox.append({"message_id": f"<r{len(inbox)}{len(sent)}@t>", "from": frm, "subject": f"Re: {tag} Gripper: version 1", "refs": "", "text": text})
        return posts.tick()

    with db.connect() as d: d.execute("INSERT INTO schedules(project_id, next_run) VALUES(%s, now() - interval '1 minute')", (pid,))
    assert posts.tick()["drafted"] == 1
    assert row()["status"] == "emailed" and len(sent) == 1 and "LI v1" in sent[0][1] and "you copy and paste this one yourself" in sent[0][1] and calls == []
    assert posts.tick()["drafted"] == 0 and len(sent) == 1  # a one-off schedule fires once
    reply("Y", frm="stranger@evil.com"); reply("Y", token="wrongtoken")
    assert row()["status"] == "emailed" and calls == []  # wrong sender / wrong secret tag: ignored
    reply("shorter please")
    assert len(sent) == 2 and "version 2" in sent[1][0] and row()["body"] == "LI revised" and row()["revisions"] == 1 and calls == []
    reply("yes but keep the numbers")
    assert len(sent) == 3 and calls == [] and row()["status"] == "emailed"  # 'yes, but...' is feedback, never approval
    reply("MY ARTICLE: I wrote this whole thing myself.")
    assert len(sent) == 4 and row()["body"] == "Owner's own article" and calls == []
    inbox.append({"message_id": "<m4@t>", "from": "me@x.com", "subject": sent[-1][0], "refs": "", "text": "copy of my own outgoing email"}); posts.tick()
    assert len(sent) == 4 and inbox == []  # our own sent copy is never treated as a reply
    reply("n")
    assert len(sent) == 5 and calls == []  # N = fresh version, still waiting
    reply("Y\n\nOn Thu, Oct 15, 2026 at 3:04 PM Me <me@x.com> wrote:\n> LI revised")
    p = row()
    assert p["status"] == "published" and calls == [("li", p["body"])] and p["result"] == {"linkedin": {"url": "https://li.com/1"}}
    assert "X text inside" in sent[-1][0] and "x v2" in sent[-1][1] and "https://li.com/1" in sent[-1][1]  # the X text arrives by email, ready to paste
    n = len(calls); posts.tick(); reply("Y")
    assert len(calls) == n  # nothing posts twice

    def new_draft(angle):  # same as the app: an autocommit connection
        d = db.connect(); d.autocommit = True; posts.draft(d, config.load(), pid, angle); d.close()
    new_draft("angle")
    def boom(cfg, text): raise RuntimeError("401 token expired")
    social.post_linkedin = boom; calls.clear(); before = len(sent)
    reply("Y")
    p = row(); assert "LinkedIn" in p["error"] and "expired" in sent[-1][1]
    assert p["status"] == "approved" and len(sent) == before + 1 and "did not accept" in sent[-1][0]  # you are told once
    posts.tick(); assert len(sent) == before + 1  # not emailed again on every retry
    social.post_linkedin = lambda cfg, text: (calls.append(("li", text)), "https://li.com/2")[1]
    out = posts.tick()
    assert row()["status"] == "published" and len(calls) == 1 and out["posted"] == 1 and "https://li.com/2" in sent[-1][1]  # a new token fixes it, posted once

    new_draft("x"); calls.clear(); del os.environ["LINKEDIN_ACCESS_TOKEN"]  # LinkedIn not connected: you still get the text
    reply("Y")
    assert calls == [] and row()["status"] == "published" and "not connected" in sent[-1][1] and "X post" in sent[-1][1]
    state["x_len"] = 400; new_draft("long"); assert "OVER THE LIMIT" in sent[-1][1]  # only a hint: X is yours to paste

    with db.connect() as d: d.execute("DELETE FROM posts; DELETE FROM schedules; DELETE FROM items; DELETE FROM projects")
    for k in ("MAIL_USER", "MAIL_PASSWORD"): del os.environ[k]

def test_websites_you_add_in_the_page():
    from fastapi.testclient import TestClient
    from megamind import config
    from megamind.connectors import website
    from megamind.main import app
    n = config.normalize_site
    assert n("example.com/work/") == "https://example.com/work" and n(" HTTP://Foo.dev/?x=1#y ") == "http://Foo.dev"
    for bad in ("localhost", "http://127.0.0.1:8000", "192.168.1.5", "10.0.0.1", "http://169.254.169.254/latest", "nodots", "x.local", ""):
        try: n(bad); assert False, bad
        except ValueError: pass  # the server fetches these, so only public website addresses
    c, ok = TestClient(app), {"Authorization": "Basic " + base64.b64encode(b"me:pw").decode()}
    assert c.post("/sites", json={"url": "localhost"}, headers=ok).status_code == 400
    assert c.post("/sites", json={"url": "good-site.com/"}, headers=ok).json() == {"url": "https://good-site.com"}
    c.post("/sites", json={"url": "https://good-site.com"}, headers=ok)  # adding twice keeps one
    os.environ["SITE1_URL"] = "https://env-site.com/"
    assert c.get("/sites", headers=ok).json() == [{"url": "https://env-site.com", "source": "vercel"}, {"url": "https://good-site.com", "source": "page"}]
    assert website.Website()._bases() == ["https://env-site.com", "https://good-site.com"]

    pages = {"https://good-site.com/sitemap.xml": b'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><sitemap><loc>https://good-site.com/s1.xml</loc></sitemap></sitemapindex>',
             "https://good-site.com/s1.xml": b'<urlset><url><loc>https://good-site.com/a</loc><lastmod>2026-01-01</lastmod></url></urlset>',
             "https://good-site.com/a": b"<html><head><title>Page A</title></head><body><p>hello</p></body></html>"}
    class Resp:
        def __init__(s, body): s.status_code, s.content, s.text, s.headers = (200 if body else 404), body or b"", (body or b"").decode(), {"content-type": "text/html"}
    class Client:
        def __init__(s, **kw): pass
        def __enter__(s): return s
        def __exit__(s, *a): pass
        def get(s, url):
            if "env-site" in url: raise ConnectionError("down")
            return Resp(pages.get(url))
    website.httpx.Client = Client
    got = []
    try:
        for it in website.Website().fetch(): got.append(it["title"])
        assert False, "an unreachable site should be reported"
    except RuntimeError as e: assert "env-site.com" in str(e)
    assert got == ["Page A"]  # the sitemap index was followed, and the broken site didn't hide the good one
    pages.clear(); pages["https://good-site.com"] = b"<html><head><title>One pager</title></head><body>solo</body></html>"; os.environ.pop("SITE1_URL")
    assert [i["title"] for i in website.Website().fetch()] == ["One pager"]  # no sitemap: the page itself
    c.delete("/sites?url=https://good-site.com", headers=ok); assert c.get("/sites", headers=ok).json() == []

def test_linkedin_sign_in_flow_and_expiry_reminder():
    from datetime import datetime, timedelta, timezone
    from fastapi.testclient import TestClient
    from urllib.parse import parse_qs, urlparse
    from megamind import config, mail, posts, social
    from megamind.main import app
    c, ok = TestClient(app, follow_redirects=False), {"Authorization": "Basic " + base64.b64encode(b"me:pw").decode()}
    assert c.get("/linkedin/connect", headers=ok).status_code == 400  # needs the Client ID and Secret first
    config.save({"LINKEDIN_CLIENT_ID": "cid", "LINKEDIN_CLIENT_SECRET": "csecret"})
    r = c.get("/linkedin/connect", headers=ok); loc = urlparse(r.headers["location"]); q_ = parse_qs(loc.query)
    assert r.status_code in (302, 307) and loc.netloc == "www.linkedin.com" and q_["client_id"] == ["cid"] and q_["scope"] == ["openid profile w_member_social"]
    assert q_["redirect_uri"][0].endswith("/linkedin/callback"); state = q_["state"][0]
    assert "scope=openid%20profile%20w_member_social" in r.headers["location"] and "+" not in loc.query  # spaces as %20, never '+'
    pv = c.get("/linkedin/connect?preview=true", headers=ok).json()["url"]; assert "state=preview" in pv and "client_id=cid" in pv
    social.httpx.post = lambda url, **kw: type("R", (), {"status_code": 401, "text": '{"error":"invalid_client_id","error_description":"Client authentication failed"}'})()
    chk = c.post("/test/linkedin_app", headers=ok).json(); assert chk["ok"] is False and "BAD KEYS" in chk["message"] and "Client authentication failed" in chk["message"]
    social.httpx.post = lambda url, **kw: type("R", (), {"status_code": 400, "text": '{"error":"invalid_request","error_description":"authorization code not found"}'})()
    assert c.post("/test/linkedin_app", headers=ok).json()["ok"] is True  # keys accepted, only the fake code was refused
    assert c.get("/linkedin/callback?code=abc&state=forged").status_code == 400  # callback needs no password but needs YOUR one-time state
    assert "expired" in c.get("/linkedin/callback?code=abc&state=forged").text
    assert "said: access_denied" not in c.get("/linkedin/callback?error=access_denied&error_description=You+cancelled").text and "You cancelled" in c.get("/linkedin/callback?error=access_denied&error_description=You+cancelled").text
    seen = []
    class R:
        status_code, text = 200, ""
        def json(s): return {"access_token": "NEWTOKEN", "expires_in": 5184000}
    social.httpx.post = lambda url, **kw: (seen.append((url, kw)), R())[1]
    ok_page = c.get(f"/linkedin/callback?code=abc&state={state}")
    assert ok_page.status_code == 200 and "LinkedIn connected" in ok_page.text and "60 days" in ok_page.text
    url, kw = seen[-1]
    assert url == "https://www.linkedin.com/oauth/v2/accessToken" and kw["data"]["code"] == "abc" and kw["data"]["client_secret"] == "csecret" and kw["data"]["redirect_uri"] == q_["redirect_uri"][0]
    assert config.load()("LINKEDIN_ACCESS_TOKEN") == "NEWTOKEN" and "NEWTOKEN" not in ok_page.text
    assert c.get(f"/linkedin/callback?code=abc&state={state}").status_code == 400  # the state works once
    st = c.get("/status", headers=ok).json(); assert st["linkedin"] and st["linkedin_expires"] and st["linkedin_redirect"] == q_["redirect_uri"][0]
    # a week before it ends you get one email
    sent = []; os.environ["MAIL_USER"], os.environ["MAIL_PASSWORD"] = "me@x.com", "pw"
    mail.send = lambda cfg, subject, body, in_reply_to=None: (sent.append(subject), "<m@t>")[1]
    now = datetime.now(timezone.utc); d = db.connect(); d.autocommit = True
    assert posts._reminders(d, config.load(), now) == 0 and sent == []  # 60 days left: nothing
    d.execute("UPDATE settings SET value=%s WHERE key='li_expires'", ((now + timedelta(days=5)).isoformat(),))
    assert posts._reminders(d, config.load(), now) == 1 and "reconnect LinkedIn" in sent[0] and posts._reminders(d, config.load(), now) == 0 and len(sent) == 1
    d.execute("DELETE FROM settings WHERE key IN ('li_expires','li_reminded','li_state')"); d.close()
    config.save({"LINKEDIN_CLIENT_ID": None, "LINKEDIN_CLIENT_SECRET": None, "LINKEDIN_ACCESS_TOKEN": None})
    for k in ("MAIL_USER", "MAIL_PASSWORD"): del os.environ[k]

def test_cron_endpoint_auth_and_settings_and_posts_endpoints():
    from fastapi.testclient import TestClient
    from megamind.main import app
    c, ok = TestClient(app), {"Authorization": "Basic " + base64.b64encode(b"me:pw").decode()}
    assert c.get("/cron/tick").status_code == 401  # no secret configured: only the password works
    os.environ["CRON_SECRET"] = "s3cret"
    assert c.get("/cron/tick", headers={"Authorization": "Bearer s3cret"}).json() == {"drafted": 0, "resent": 0, "replies": 0, "posted": 0, "reminders": 0}
    assert c.get("/cron/tick", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert c.get("/sync", headers={"Authorization": "Bearer s3cret"}).status_code == 401  # the cron secret opens only /cron/tick
    del os.environ["CRON_SECRET"]
    st = c.get("/status", headers=ok).json()  # feeds the Start-here checklist
    assert st["ai"] is False and st["email"] is False and "x" not in st and st["last_check"] is not None and st["items"] == 0 and "/cron/tick?key=" in st["check_url"]
    assert {"awaiting", "next_run", "last_posted", "posted", "scheduled"} <= set(st)
    for f in ("logo", "mark", "favicon"):
        r = c.get(f"/static/{f}.png"); assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content[:4] == b"\x89PNG"  # public: no password for the logo
    assert c.get("/static/index.html", headers=ok).status_code == 404 and c.get("/static/../main.py").status_code in (401, 404)  # only the three logo files
    key = st["check_url"].split("key=")[1]
    assert c.get(f"/cron/tick?key={key}").status_code == 200 and c.get("/cron/tick?key=wrong").status_code == 401  # the private link works without the password
    assert c.get(f"/sync?key={key}").status_code == 401
    assert c.post("/test/email", headers=ok).json()["ok"] is False and "app password" in c.post("/test/email", headers=ok).json()["message"]
    from megamind import llm; llm.ask = lambda prompt, cfg=None: "ok"
    assert c.post("/test/ai", headers=ok).json() == {"ok": True, "message": "The AI replied: ok"}
    assert c.post("/test/linkedin", headers=ok).json()["ok"] is False and c.post("/test/nonsense", headers=ok).status_code == 404
    r = c.put("/settings", json={"LLM_API_KEY": "sk-very-secret", "LLM_PROVIDER": "groq"}, headers=ok).json()
    assert "sk-very-secret" not in str(r) and r["fields"]["LLM_API_KEY"]["set"] and r["providers"]["gemini"].endswith("/v1beta/openai")
    assert c.put("/settings", json={"BOGUS": "x"}, headers=ok).status_code == 400
    c.put("/settings", json={"LLM_API_KEY": None, "LLM_PROVIDER": None}, headers=ok)
    run_at = "2030-01-01T00:00:00Z"
    sid = c.post("/schedules", json={"run_at": run_at, "every_days": 7, "angle": "demo"}, headers=ok).json()[0]["id"]
    assert [s["angle"] for s in c.get("/schedules", headers=ok).json()] == ["demo"]
    c.delete(f"/schedules/{sid}", headers=ok); assert c.get("/schedules", headers=ok).json() == []
    d = db.connect(); d.execute("INSERT INTO posts(body, x_body, status, token) VALUES('b','x','emailed','topsecret')"); d.commit(); d.close()
    posts_json = c.get("/posts", headers=ok).json(); assert posts_json[0]["status"] == "emailed" and "topsecret" not in str(posts_json)  # token never leaves the server
    c.post(f"/posts/{posts_json[0]['id']}/discard", headers=ok); assert c.get("/posts", headers=ok).json()[0]["status"] == "rejected"
    d = db.connect(); d.execute("DELETE FROM posts"); d.commit(); d.close()
    assert c.post("/posts", json={}, headers=ok).status_code == 400  # no AI key / no projects: a clear error, not a crash

if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"): f(); print("ok", n)
