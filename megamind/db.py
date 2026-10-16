import os
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

# One generic items table: new sources/types fit via `type` + `metadata`, no schema change.
# The pgvector `embedding` column is added separately (connect) so the app still runs where the extension is missing.
SCHEMA = """
SELECT pg_advisory_xact_lock(7001);  -- parallel cold starts must not race on CREATE
CREATE TABLE IF NOT EXISTS projects(id SERIAL PRIMARY KEY, name TEXT, summary TEXT DEFAULT '', status TEXT DEFAULT 'active');
CREATE TABLE IF NOT EXISTS items(
  id SERIAL PRIMARY KEY, source TEXT NOT NULL, ext_id TEXT NOT NULL, type TEXT DEFAULT 'item',
  title TEXT, description TEXT DEFAULT '', date TIMESTAMPTZ, url TEXT,
  media JSONB DEFAULT '[]', tags TEXT[] DEFAULT '{}', metadata JSONB DEFAULT '{}',
  project_id INT REFERENCES projects(id) ON DELETE SET NULL, edited BOOLEAN DEFAULT false,
  fts TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', coalesce(title,'') || ' ' || coalesce(description,''))) STORED,
  UNIQUE(source, ext_id));
CREATE INDEX IF NOT EXISTS items_fts ON items USING gin(fts);
CREATE TABLE IF NOT EXISTS sync_runs(source TEXT, started TIMESTAMPTZ DEFAULT now(), ok BOOLEAN, count INT, error TEXT);
CREATE TABLE IF NOT EXISTS posts(
  id SERIAL PRIMARY KEY, project_id INT REFERENCES projects(id) ON DELETE SET NULL, angle TEXT, body TEXT, x_body TEXT DEFAULT '',
  status TEXT DEFAULT 'draft',  -- draft | emailed | approved | published | rejected
  scheduled_for TIMESTAMPTZ, published_at TIMESTAMPTZ, created TIMESTAMPTZ DEFAULT now());
CREATE TABLE IF NOT EXISTS corrections(id SERIAL PRIMARY KEY, kind TEXT, item_id INT, project_id INT, note TEXT);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
-- ignored: NULL = not decided yet, true = hidden everywhere (search, grouping, embeddings), false = decided to keep
ALTER TABLE items ADD COLUMN IF NOT EXISTS ignored BOOLEAN;
-- posts: draft (not emailed yet) -> emailed (waiting for your reply) -> approved (you said yes; posting) -> published | rejected
ALTER TABLE posts ADD COLUMN IF NOT EXISTS token TEXT, ADD COLUMN IF NOT EXISTS revisions INT DEFAULT 0, ADD COLUMN IF NOT EXISTS history JSONB DEFAULT '[]',
  ADD COLUMN IF NOT EXISTS result JSONB DEFAULT '{}', ADD COLUMN IF NOT EXISTS error TEXT, ADD COLUMN IF NOT EXISTS schedule_id INT;
CREATE TABLE IF NOT EXISTS schedules(id SERIAL PRIMARY KEY, project_id INT REFERENCES projects(id) ON DELETE SET NULL,  -- NULL = pick one automatically
  angle TEXT DEFAULT '', next_run TIMESTAMPTZ NOT NULL, every_days INT, active BOOLEAN DEFAULT true, last_error TEXT);
"""
COLS = "id, source, type, title, description, date, url, media, tags, metadata, project_id, edited, ignored"
_ready = False
VECTOR = False  # True once pgvector + items.embedding exist

def connect():
    global _ready, VECTOR
    # Vercel's Neon integration sets DATABASE_URL (and POSTGRES_URL). prepare_threshold=None keeps Neon's pooler happy.
    db = psycopg.connect(os.environ.get("DATABASE_URL") or os.environ["POSTGRES_URL"],
                         row_factory=dict_row, prepare_threshold=None)
    if not _ready:
        db.execute(SCHEMA); db.commit()
        try:
            db.execute("SELECT pg_advisory_xact_lock(7002); CREATE EXTENSION IF NOT EXISTS vector; "
                       "ALTER TABLE items ADD COLUMN IF NOT EXISTS embedding vector(1024)")
            db.commit(); VECTOR = True
        except psycopg.Error:
            db.rollback()
        _ready = True
    return db

def upsert(db, source, it):
    # re-sync refreshes content but never touches project_id or items you edited by hand;
    # changed text clears the embedding so it gets re-embedded
    stale = ("embedding = CASE WHEN items.title IS NOT DISTINCT FROM excluded.title AND items.description IS NOT DISTINCT FROM "
             "excluded.description THEN items.embedding ELSE NULL END, ") if VECTOR else ""
    db.execute(
        """INSERT INTO items(source,ext_id,type,title,description,date,url,media,tags,metadata) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT(source,ext_id) DO UPDATE SET """ + stale + """type=excluded.type, title=excluded.title, description=excluded.description,
             date=excluded.date, url=excluded.url, media=excluded.media, tags=excluded.tags, metadata=excluded.metadata
           WHERE NOT items.edited""",
        (source, it["ext_id"], it.get("type", "item"), it["title"], it.get("description", ""), it.get("date"), it.get("url"),
         Jsonb(it.get("media", [])), it.get("tags", []), Jsonb(it.get("metadata", {}))))

def q(sql, params=None):
    with connect() as db:
        return db.execute(sql, params).fetchall()

DEFAULT_IGNORE = "Tasks|College packing list"  # Notion titles (case-insensitive) to skip; stored in settings.ignore_titles, edit it there

def apply_ignore(db):
    """Run after every sync. Decides each undecided item once: ignored if it is a Notion page on the ignore list, sits in a Notion
    database on the list, or is nested under an ignored page; otherwise kept. Choices you make with the buttons are never overridden."""
    db.execute("INSERT INTO settings VALUES('ignore_titles', %s) ON CONFLICT DO NOTHING", (DEFAULT_IGNORE,))
    rules = [x.strip().lower() for x in db.execute("SELECT value FROM settings WHERE key='ignore_titles'").fetchone()["value"].split("|") if x.strip()]
    db.execute("UPDATE items SET ignored=true WHERE ignored IS NULL AND source='notion' "
               "AND (lower(title) = ANY(%(r)s) OR lower(metadata->>'parent_title') = ANY(%(r)s))", {"r": rules})
    db.execute("""WITH RECURSIVE d AS (SELECT ext_id FROM items WHERE source='notion' AND ignored
                    UNION SELECT c.ext_id FROM items c JOIN d ON c.source='notion' AND c.metadata->>'parent' = d.ext_id)
                  UPDATE items SET ignored=true WHERE ignored IS NULL AND source='notion' AND ext_id IN (SELECT ext_id FROM d)""")
    db.execute("UPDATE items SET ignored=false WHERE ignored IS NULL")

def set_ignored(db, id, on):
    """Button handler. A Notion page is ignored/restored together with everything nested under it."""
    return db.execute("""WITH RECURSIVE d AS (SELECT source, ext_id FROM items WHERE id=%s
                           UNION SELECT c.source, c.ext_id FROM items c JOIN d ON d.source='notion' AND c.source='notion' AND c.metadata->>'parent' = d.ext_id)
                         UPDATE items SET ignored=%s WHERE (source, ext_id) IN (SELECT source, ext_id FROM d)""", (id, on)).rowcount
