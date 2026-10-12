import datetime as dt, json, os, sys
from .db import connect, upsert
from .connectors import CONNECTORS

def run(source=None):
    db, out = connect(), {}
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
        db.execute("INSERT INTO sync_runs VALUES(?,?,?,?,?)",
                   (c.name, dt.datetime.now(dt.UTC).isoformat(timespec="seconds"), err is None, n, err))
        db.commit()
        out[c.name] = err or n
    return out

if __name__ == "__main__":
    print(json.dumps(run(*sys.argv[1:2]), indent=2))
