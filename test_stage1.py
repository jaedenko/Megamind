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

def test_ask_uses_opencode_when_keyed_else_anthropic():
    from megamind import cluster
    seen = []
    class R:
        def __init__(s, j): s.j = j
        def raise_for_status(s): return s
        def json(s): return s.j
    cluster.httpx.post = lambda url, **kw: (seen.append((url, kw)), R({"choices": [{"message": {"content": "hi"}}], "content": [{"text": "claude"}]}))[1]
    for k in ("OPENCODE_API_KEY", "LLM_MODEL", "ANTHROPIC_API_KEY"): os.environ.pop(k, None)
    assert not cluster.on()
    os.environ["OPENCODE_API_KEY"] = "ock"
    assert cluster.on()
    try: cluster.ask("x"); assert False
    except RuntimeError as e: assert "LLM_MODEL" in str(e)  # tells you where to find model ids
    os.environ["LLM_MODEL"] = "some-model"
    assert cluster.ask("x") == "hi"
    url, kw = seen[-1]
    assert url == "https://opencode.ai/zen/v1/chat/completions" and kw["headers"]["Authorization"] == "Bearer ock" and kw["json"]["model"] == "some-model"
    del os.environ["OPENCODE_API_KEY"]; os.environ["ANTHROPIC_API_KEY"] = "ak"
    assert cluster.ask("x") == "claude" and seen[-1][0] == "https://api.anthropic.com/v1/messages"
    for k in ("LLM_MODEL", "ANTHROPIC_API_KEY"): os.environ.pop(k, None)

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

if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"): f(); print("ok", n)
