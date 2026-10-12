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
  project_id INTEGER REFERENCES projects(id), edited INTEGER DEFAULT 0, UNIQUE(source, ext_id));
CREATE TABLE IF NOT EXISTS sync_runs(source TEXT, started TEXT, ok INTEGER, count INTEGER, error TEXT);
"""

def connect():
    db = sqlite3.connect(os.environ.get("MEGAMIND_DB", "megamind.db"))
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
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
        for k in ("media", "tags"):
            if k in r: r[k] = json.loads(r[k])
    return rows
