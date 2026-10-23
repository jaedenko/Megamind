import ipaddress, json, os, re
from urllib.parse import urlparse
from .db import connect

# Every setting can come from the Settings box in the web page (stored in the settings table) or from a Vercel env var of the same name.
# The page wins, so keys can be rotated without a redeploy. Value = is it a secret (never sent back to the browser).
FIELDS = {"LLM_PROVIDER": False, "LLM_BASE_URL": False, "LLM_MODEL": False, "LLM_API_KEY": True,
          "MAIL_USER": False, "MAIL_PASSWORD": True, "MAIL_TO": False, "SMTP_HOST": False, "IMAP_HOST": False,
          "LINKEDIN_CLIENT_ID": False, "LINKEDIN_CLIENT_SECRET": True, "LINKEDIN_ACCESS_TOKEN": True}

def load():
    """-> cfg(name, default=None): saved value, else env var, else default. One DB read per call of load()."""
    with connect() as db:
        saved = {r["key"][4:]: r["value"] for r in db.execute("SELECT key, value FROM settings WHERE key LIKE 'cfg:%'").fetchall()}
    return lambda name, default=None: saved.get(name) or os.environ.get(name) or default

def status():
    with connect() as db:
        saved = {r["key"][4:]: r["value"] for r in db.execute("SELECT key, value FROM settings WHERE key LIKE 'cfg:%'").fetchall()}
    out = {}
    for name, secret in FIELDS.items():
        src = "page" if saved.get(name) else "env" if os.environ.get(name) else None
        out[name] = {"secret": secret, "source": src, "set": src is not None,
                     "value": None if secret or not src else (saved.get(name) or os.environ.get(name))}
    return out

def save(values):
    """{name: "text"} sets, {name: null} clears, {name: ""} leaves unchanged (an untouched form field)."""
    with connect() as db:
        for name, v in values.items():
            if name not in FIELDS: raise KeyError(name)
            if v is None: db.execute("DELETE FROM settings WHERE key=%s", ("cfg:" + name,))
            elif str(v).strip(): db.execute("INSERT INTO settings VALUES(%s,%s) ON CONFLICT(key) DO UPDATE SET value=excluded.value", ("cfg:" + name, str(v).strip()))
        if any(n.startswith("LLM_") and v is not None and str(v).strip() or n.startswith("LLM_") and v is None for n, v in values.items()):
            db.execute("DELETE FROM settings WHERE key IN ('llm_cooldown','llm_strikes','llm_down_since','llm_alerted')")  # new model/provider/key: fresh start

def cron_key():
    """A private random key, made on first use, that lets a free pinger call /cron/tick?key=... without the site password."""
    import secrets
    with connect() as db:
        db.execute("INSERT INTO settings VALUES('cron_key', %s) ON CONFLICT DO NOTHING", (secrets.token_urlsafe(24),))
        return db.execute("SELECT value FROM settings WHERE key='cron_key'").fetchone()["value"]

def normalize_site(url):
    """'example.com/work/' -> 'https://example.com/work'. Refuses anything that isn't a public website address
    (the server fetches these, so no localhost / private-network addresses; DNS names are not resolved here)."""
    u = (url or "").strip()
    if not re.match(r"(?i)https?://", u): u = "https://" + u
    p = urlparse(u); host = (p.hostname or "").lower()
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified: raise ValueError("That is a private address. Use your public website address.")
    except ValueError as e:
        if "private" in str(e): raise
    if "." not in host or host.endswith((".local", ".internal", ".localhost")): raise ValueError("That doesn't look like a full website address, e.g. https://yourname.com")
    return f"{p.scheme}://{p.netloc}{p.path.rstrip('/')}"

def sites():
    with connect() as db:
        r = db.execute("SELECT value FROM settings WHERE key='sites'").fetchone()
    return json.loads(r["value"]) if r else []

def _save_sites(lst):
    with connect() as db:
        db.execute("INSERT INTO settings VALUES('sites', %s) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(lst),))

def add_site(url):
    u = normalize_site(url); lst = sites()
    if u not in lst: _save_sites(lst + [u])
    return u

def remove_site(url):
    _save_sites([x for x in sites() if x != url])
