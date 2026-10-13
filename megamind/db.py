import json, os, sqlite3
from pathlib import Path

def load_env(path=".env"):
    if Path(path).exists():
        for line in Path(path).read_text().splitlines():
            k, _, v = line.partition("=")
            if v.strip() and not k.startswith("#"):
                os.environ.setdefault(k.strip(), v.strip().strip("\"'"))

load_env()

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects(id INTEGER PRIMARY KEY, name TEXT, summary TEXT DEFAULT '', status TEXT DEFAULT 'active');
CREATE TABLE IF NOT EXISTS items(
  id INTEGER PRIMARY KEY, source TEXT NOT NULL, ext_id TEXT NOT NULL, title TEXT, description TEXT,
  date TEXT, url TEXT, media TEXT DEFAULT '[]', tags TEXT DEFAULT '[]',
  project_id INTEGER REFERENCES projects(id), edited INTEGER DEFAULT 0, embedding BLOB, UNIQUE(source, ext_id));
CREATE TABLE IF NOT EXISTS sync_runs(source TEXT, started TEXT, ok INTEGER, count INTEGER, error TEXT);
CREATE TABLE IF NOT EXISTS posts(
  id INTEGER PRIMARY KEY, project_id INTEGER REFERENCES projects(id), angle TEXT, body TEXT, x_body TEXT DEFAULT '',
  status TEXT DEFAULT 'draft',  -- draft | emailed | approved | published | rejected
  scheduled_for TEXT, published_at TEXT, created TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS corrections(id INTEGER PRIMARY KEY, kind TEXT, item_id INTEGER, project_id INTEGER, note TEXT);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
"""

def connect():
    db = sqlite3.connect(os.environ.get("MEGAMIND_DB", "megamind.db"))
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    try: db.execute("ALTER TABLE items ADD COLUMN embedding BLOB")  # DBs created before stage 1b
    except sqlite3.OperationalError: pass
    return db

def upsert(db, source, it):
    # re-sync refreshes content but never touches project_id or items you edited by hand
    db.execute(
        """INSERT INTO items(source,ext_id,title,description,date,url,media,tags) VALUES(?,?,?,?,?,?,?,?)
           ON CONFLICT(source,ext_id) DO UPDATE SET title=excluded.title, description=excluded.description,
             date=excluded.date, url=excluded.url, media=excluded.media, tags=excluded.tags
           WHERE items.edited=0""",
        (source, it["ext_id"], it["title"], it.get("description", ""), it.get("date"), it.get("url"),
         json.dumps(it.get("media", [])), json.dumps(it.get("tags", []))))

def q(sql, *args):
    db = connect()
    rows = [dict(r) for r in db.execute(sql, args)]
    for r in rows:
        r.pop("embedding", None)  # raw vectors are for search, not for API output
        for k in ("media", "tags"):
            if k in r: r[k] = json.loads(r[k])
    return rows
