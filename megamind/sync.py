import os
from . import embed
from .db import apply_ignore, connect, upsert
from .connectors import CONNECTORS

def run(source=None):
    db, out = connect(), {}
    db.autocommit = True  # each item commits alone: one bad row can't poison or discard the rest
    off = os.environ.get("MEGAMIND_DISABLE", "").split(",")
    for c in CONNECTORS:
        if source and c.name != source:
            continue
        if c.name in off or not c.configured():
            out[c.name] = "disabled or not configured"
            continue
        n, err = 0, None
        try:
            for it in c.fetch():
                upsert(db, c.name, it); n += 1
        except Exception as e:
            err = repr(e)
        db.execute("INSERT INTO sync_runs(source,ok,count,error) VALUES(%s,%s,%s,%s)", (c.name, err is None, n, err))
        out[c.name] = err or n
    apply_ignore(db)
    if embed.on():
        try:
            out["embeddings"] = embed.pending(db)
        except Exception as e:  # sync still succeeded; unembedded items are retried next sync
            out["embeddings"] = repr(e)
    db.close()
    return out
