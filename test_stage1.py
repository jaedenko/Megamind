import os, tempfile
os.environ["MEGAMIND_DB"] = tempfile.mktemp(suffix=".db")
from megamind import sync, db
from megamind.connectors.website import extract, _rows

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
        def fetch(s): yield {"ext_id": "1", "title": s.v[0]}
    f = Fake(); sync.CONNECTORS[:] = [f]
    assert sync.run() == {"fake": 1}
    d = db.connect(); d.execute("UPDATE items SET edited=1, title='mine'"); d.commit()
    f.v[0] = "two"; sync.run()
    rows = db.q("SELECT title FROM items")
    assert rows == [{"title": "mine"}]  # edited item untouched, no duplicate row

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

def test_schema_has_post_tables_and_hides_embeddings():
    d = db.connect()
    names = {r[0] for r in d.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"items", "projects", "posts", "corrections", "settings", "sync_runs"} <= names
    d.execute("INSERT INTO items(source,ext_id,title,embedding) VALUES('t','e','x',?)", (b"\x00\x01",)); d.commit()
    assert all("embedding" not in r for r in db.q("SELECT * FROM items"))

def test_api_sync_lists_every_connector_and_serves_page():
    from fastapi.testclient import TestClient
    from megamind.main import app
    from megamind.connectors import CONNECTORS, GitHub, Notion, Website
    CONNECTORS[:] = [GitHub(), Notion(), Website()]  # same list object sync uses; an earlier test swapped in a fake
    c = TestClient(app)
    assert {r["source"] for r in c.get("/sync").json()} == {"github", "notion", "website"}
    assert c.get("/").status_code == 200 and c.get("/projects").json() == []

if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"): f(); print("ok", n)
