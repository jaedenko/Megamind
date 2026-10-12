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

if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"): f(); print("ok", n)
