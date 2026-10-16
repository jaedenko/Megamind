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
    os.environ["X_API_KEY"] = "from-env"
    assert config.load()("X_API_KEY") == "from-env" and config.status()["X_API_KEY"] == {"secret": True, "source": "env", "set": True, "value": None}
    config.save({"X_API_KEY": "from-page", "MAIL_TO": "me@x.com", "LLM_MODEL": ""})  # "" = untouched form field
    assert config.load()("X_API_KEY") == "from-page" and config.status()["MAIL_TO"]["value"] == "me@x.com"
    assert "from-page" not in str(config.status()) and config.status()["LLM_MODEL"]["set"] is False
    try: config.save({"NOT_A_SETTING": "x"}); assert False
    except KeyError: pass
    config.save({"X_API_KEY": None, "MAIL_TO": None}); del os.environ["X_API_KEY"]
    assert config.load()("X_API_KEY") is None

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

def test_oauth1_signature_matches_twitters_documented_example():
    from megamind.social import oauth1
    h = oauth1("POST", "https://api.twitter.com/1.1/statuses/update.json", "xvz1evFS4wEEPTGEFPHBog", "kAcSOqF21Fu85e7zjz7ZN2U4ZRhfV3WpwPAoE3Z7kBw",
               "370773112-GmHxMAgYyLbNEtIKZeRNFsMKPR9EyMZeS9weJAEb", "LswwdoUaIvS8ltyTt5jkRh4J50vUPVVHtR2YPi5kE",
               extra={"include_entities": "true", "status": "Hello Ladies + Gentlemen, a signed OAuth request!"},
               nonce="kYjzVBB8Y0ZFabxSWbWovY3uYSQ2pTgmZeNu2VS4cg", ts="1318622958")
    assert 'oauth_signature="hCtSmYh%2BiHYCEqBWrE7C7hYmtUk%3D"' in h

def test_social_requests_have_the_right_shape():
    from megamind import social
    seen = []
    class R:
        status_code, text = 201, ""
        def __init__(s, j=None, h=None): s.j, s.headers = j or {}, h or {}
        def json(s): return s.j
    social.httpx.post = lambda url, **kw: (seen.append(("POST", url, kw)), R({"data": {"id": "99"}}, {"x-restli-id": "urn:li:share:7"}))[1]
    social.httpx.get = lambda url, **kw: (seen.append(("GET", url, kw)), R({"sub": "abc123"}))[1]
    cfg = {"X_API_KEY": "a", "X_API_SECRET": "b", "X_ACCESS_TOKEN": "c", "X_ACCESS_SECRET": "d", "LINKEDIN_ACCESS_TOKEN": "tok"}.get
    assert social.post_x(cfg, "hi") == "https://x.com/i/web/status/99"
    _, url, kw = seen[-1]; assert url == "https://api.x.com/2/tweets" and kw["json"] == {"text": "hi"} and kw["headers"]["Authorization"].startswith("OAuth ")
    assert social.post_linkedin(cfg, "a_b #1 (x)") == "https://www.linkedin.com/feed/update/urn:li:share:7/"
    _, url, kw = seen[-1]
    assert url == "https://api.linkedin.com/rest/posts" and kw["json"]["author"] == "urn:li:person:abc123" and kw["json"]["commentary"] == "a\\_b \\#1 \\(x\\)"
    assert kw["headers"]["Authorization"] == "Bearer tok" and kw["headers"]["X-Restli-Protocol-Version"] == "2.0.0" and kw["headers"]["LinkedIn-Version"]

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
    for k, v in {"MAIL_USER": "me@x.com", "MAIL_PASSWORD": "pw", "X_API_KEY": "1", "X_API_SECRET": "2", "X_ACCESS_TOKEN": "3", "X_ACCESS_SECRET": "4", "LINKEDIN_ACCESS_TOKEN": "5"}.items(): os.environ[k] = v
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
    social.post_x = lambda cfg, text: (calls.append(("x", text)), "https://x.com/1")[1]
    social.post_linkedin = lambda cfg, text: (calls.append(("li", text)), "https://li.com/1")[1]
    row = lambda: db.q("SELECT * FROM posts ORDER BY id DESC LIMIT 1")[0]
    def reply(text, frm="me@x.com", token=None, subj=None):
        tag = re.search(r"\[Megamind post-\d+-[a-z0-9]+\]", sent[-1][0])[0]
        if token: tag = re.sub(r"-[a-z0-9]+\]", f"-{token}]", tag)
        inbox.append({"message_id": f"<r{len(inbox)}{len(sent)}@t>", "from": frm, "subject": subj or f"Re: {tag} Gripper: version 1", "refs": "", "text": text})
        return posts.tick()

    with db.connect() as d: d.execute("INSERT INTO schedules(project_id, next_run) VALUES(%s, now() - interval '1 minute')", (pid,))
    assert posts.tick()["drafted"] == 1
    assert row()["status"] == "emailed" and len(sent) == 1 and "LI v1" in sent[0][1] and "Nothing is posted until you reply Y" in sent[0][1] and calls == []
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
    assert p["status"] == "published" and sorted(calls) == [("li", "LI revised"), ("x", "x v2")] and set(p["result"]) == {"x", "linkedin"}
    assert "Posted" in sent[-1][0]
    n = len(calls); posts.tick(); reply("Y")
    assert len(calls) == n  # nothing posts twice

    def new_draft(angle):  # same as the app: an autocommit connection
        d = db.connect(); d.autocommit = True; posts.draft(d, config.load(), pid, angle); d.close()
    new_draft("angle")
    def boom(cfg, text): raise RuntimeError("402 no credits")
    social.post_x = boom; calls.clear()
    reply("Y")
    p = row()
    assert p["status"] == "approved" and "x:" in p["error"] and [c[0] for c in calls] == ["li"]
    social.post_x = lambda cfg, text: (calls.append(("x", text)), "https://x.com/2")[1]
    out = posts.tick()
    assert row()["status"] == "published" and [c[0] for c in calls] == ["li", "x"] and out["posted"] == 1  # only the failed platform is retried

    state["x_len"] = 400; new_draft(""); calls.clear()
    assert "OVER THE LIMIT" in sent[-1][1]
    reply("Y")
    assert calls == [] and row()["status"] == "emailed" and "OVER THE LIMIT" in sent[-1][1]  # a Y can't post an over-length draft

    with db.connect() as d: d.execute("DELETE FROM posts; DELETE FROM schedules; DELETE FROM items; DELETE FROM projects")
    for k in ("MAIL_USER", "MAIL_PASSWORD", "X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET", "LINKEDIN_ACCESS_TOKEN"): del os.environ[k]

def test_cron_endpoint_auth_and_settings_and_posts_endpoints():
    from fastapi.testclient import TestClient
    from megamind.main import app
    c, ok = TestClient(app), {"Authorization": "Basic " + base64.b64encode(b"me:pw").decode()}
    assert c.get("/cron/tick").status_code == 401  # no secret configured: only the password works
    os.environ["CRON_SECRET"] = "s3cret"
    assert c.get("/cron/tick", headers={"Authorization": "Bearer s3cret"}).json() == {"drafted": 0, "resent": 0, "replies": 0, "posted": 0}
    assert c.get("/cron/tick", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert c.get("/sync", headers={"Authorization": "Bearer s3cret"}).status_code == 401  # the cron secret opens only /cron/tick
    del os.environ["CRON_SECRET"]
    r = c.put("/settings", json={"LLM_API_KEY": "sk-very-secret", "LLM_PROVIDER": "groq"}, headers=ok).json()
    assert "sk-very-secret" not in str(r) and r["fields"]["LLM_API_KEY"]["set"] and "groq" in r["providers"]
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
