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

def test_schema_has_generic_columns_and_fulltext():
    names = {r["table_name"] for r in db.q("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")}
    assert {"items", "projects", "posts", "corrections", "settings", "sync_runs"} <= names
    assert db.q("SELECT id FROM items WHERE fts @@ websearch_to_tsquery('english','mine')")  # title of the edited item above

def test_cluster_parse_trusts_only_ids_we_sent():
    from megamind.cluster import parse
    reply = 'sure! {"projects":[{"id":9,"name":"Gripper","summary":"s","items":[1,2,99,"x",[3]]},{"id":null,"name":"Ghost","items":[99]},{"id":2,"name":"Old","items":[3]}]}'
    # 99 wasn't sent, id 9 isn't a known project (-> new), Ghost has no valid items (dropped), id 2 is known (reused)
    assert parse(reply, {1, 2, 3}, {2}) == [(None, "Gripper", "s", [1, 2]), (2, "Old", "", [3])]

def test_cluster_fills_only_unassigned():
    from megamind import cluster
    d = db.connect(); mine = d.execute("INSERT INTO projects(name) VALUES('Mine') RETURNING id").fetchone()["id"]
    ids = [d.execute("INSERT INTO items(source,ext_id,title,project_id) VALUES('t',%s,%s,%s) RETURNING id", (n, n, mine if n == "c" else None)).fetchone()["id"] for n in "abc"]
    d.commit(); d.close()
    cluster.ask = lambda prompt: '{"projects":[{"id":null,"name":"New","summary":"","items":%s}]}' % ids  # also tries to grab the manual one
    assert cluster.run() == {"new_projects": 1, "assigned": 2}
    got = [r["project_id"] for r in db.q("SELECT project_id FROM items WHERE source='t' ORDER BY id")]
    assert got[0] == got[1] != mine and got[2] == mine  # manual assignment survives
    assert cluster.run() == {"new_projects": 0, "assigned": 0}  # nothing unassigned -> no model call
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

if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"): f(); print("ok", n)
