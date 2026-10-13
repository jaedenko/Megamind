import os
import httpx
from . import db

def on():
    if not db._ready: db.connect().close()  # cold start: VECTOR is only known after the schema step
    return bool(os.environ.get("VOYAGE_API_KEY")) and db.VECTOR

def embed(texts, kind="document"):
    r = httpx.post("https://api.voyageai.com/v1/embeddings", timeout=60, json={"input": texts, "model": "voyage-3.5", "input_type": kind},
                   headers={"Authorization": "Bearer " + os.environ["VOYAGE_API_KEY"]})
    r.raise_for_status()
    vecs = [d["embedding"] for d in r.json()["data"]]
    assert len(vecs) == len(texts)
    return vecs

def pending(conn):
    """Embed every item that has no embedding yet; returns how many."""
    n = 0
    while rows := conn.execute("SELECT id, title, description FROM items WHERE embedding IS NULL ORDER BY id LIMIT 64").fetchall():
        for r, v in zip(rows, embed([f"{r['title']}\n{r['description']}"[:8000] for r in rows])):
            conn.execute("UPDATE items SET embedding=%s::vector WHERE id=%s", (str(v), r["id"]))
        n += len(rows)
    return n

def cluster(conn, max_dist=0.35):
    """Put unassigned items into the nearest project (by centroid), else start a new one named after the item."""
    # ponytail: greedy, recomputes centroids per item (fine at personal scale); tune max_dist, rename projects by hand
    placed = made = 0
    for it in conn.execute("SELECT id, title, embedding FROM items WHERE project_id IS NULL AND embedding IS NOT NULL ORDER BY date NULLS LAST, id").fetchall():
        near = conn.execute("SELECT project_id, avg(embedding) <=> %s::vector AS d FROM items WHERE project_id IS NOT NULL AND embedding IS NOT NULL "
                            "GROUP BY project_id ORDER BY d LIMIT 1", (it["embedding"],)).fetchone()
        if near and near["d"] <= max_dist:
            pid = near["project_id"]
        else:
            pid = conn.execute("INSERT INTO projects(name) VALUES(%s) RETURNING id", (it["title"] or "Untitled",)).fetchone()["id"]; made += 1
        conn.execute("UPDATE items SET project_id=%s WHERE id=%s", (pid, it["id"])); placed += 1
    return {"items_placed": placed, "projects_created": made}
