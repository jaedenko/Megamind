import json, logging, re, time
import httpx
from . import config

# provider -> (base url, wire format). "custom" = any OpenAI-compatible server: give LLM_BASE_URL.
PRESETS = {"openai": ("https://api.openai.com/v1", "openai"), "openrouter": ("https://openrouter.ai/api/v1", "openai"),
           "groq": ("https://api.groq.com/openai/v1", "openai"), "together": ("https://api.together.xyz/v1", "openai"),
           "deepseek": ("https://api.deepseek.com/v1", "openai"), "mistral": ("https://api.mistral.ai/v1", "openai"),
           "xai": ("https://api.x.ai/v1", "openai"), "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "openai"),
           "opencode": ("https://opencode.ai/zen/v1", "openai"), "opencode-go": ("https://opencode.ai/zen/go/v1", "openai"),
           "anthropic": ("https://api.anthropic.com", "anthropic"), "custom": (None, "openai")}

def settings(cfg):
    key, provider = cfg("LLM_API_KEY"), cfg("LLM_PROVIDER")
    if not key and cfg("OPENCODE_API_KEY"): key, provider = cfg("OPENCODE_API_KEY"), provider or "opencode"  # older env names
    if not key and cfg("ANTHROPIC_API_KEY"): key, provider = cfg("ANTHROPIC_API_KEY"), provider or "anthropic"
    if not key: return None
    if (provider or "openai") not in PRESETS: raise RuntimeError(f"Unknown LLM_PROVIDER {provider!r}; pick one of {', '.join(PRESETS)}")
    base, fmt = PRESETS[provider or "openai"]
    base = (cfg("LLM_BASE_URL") or base or "").rstrip("/")
    if not base: raise RuntimeError("Provider 'custom' needs LLM_BASE_URL (the server's OpenAI-compatible base, e.g. https://host/v1)")
    return {"key": key, "base": base, "fmt": fmt, "model": cfg("LLM_MODEL") or ("claude-sonnet-5-5" if fmt == "anthropic" else None)}

def on(cfg=None):
    cfg = cfg or config.load()
    return bool(cfg("LLM_API_KEY") or cfg("OPENCODE_API_KEY") or cfg("ANTHROPIC_API_KEY"))

log = logging.getLogger("megamind.llm")
COOLDOWN, MAX_COOLDOWN, ALERT_AFTER = 600, 3600, 3600  # seconds: first back-off, back-off ceiling, down this long -> email you

def _db(sql, *args):
    """Run one statement on the settings table (state lives there so every cron tick sees it); returns rows if it has any."""
    from .db import connect
    with connect() as db:
        cur = db.execute(sql, args or None)
        return cur.fetchall() if cur.description else []

def _cooling():
    """Seconds left on the provider cooldown (0 = go ahead)."""
    r = _db("SELECT EXTRACT(EPOCH FROM (value::timestamptz - now())) AS left_s FROM settings WHERE key='llm_cooldown'")
    return max(0, int(r[0]["left_s"])) if r and r[0]["left_s"] else 0

def _secs(header, default):
    try: return float(header)
    except (TypeError, ValueError): return default

def _tell(cfg, subject, body):
    from . import mail
    if not mail.configured(cfg): return
    try: mail.send(cfg, subject, body)
    except Exception as e: log.warning("llm: could not email the alert: %r", e)

def _fail(cfg, s, r):
    """The provider still refused after our short retries. Log why, back off longer each time (ceiling 1h), and if it stays down for
    ALERT_AFTER, email once with the reason so a human sees it. Bookkeeping errors are logged, never raised: the caller reports the real failure."""
    try:
        strikes = int(_db("INSERT INTO settings VALUES('llm_strikes','1') ON CONFLICT(key) DO UPDATE SET value=(settings.value::int+1)::text RETURNING value")[0]["value"])
        wait = int(min(max(_secs(r.headers.get("retry-after"), 0), COOLDOWN * 2 ** (strikes - 1)), MAX_COOLDOWN))
        _db("INSERT INTO settings VALUES('llm_cooldown', (now() + make_interval(secs => %s))::text) ON CONFLICT(key) DO UPDATE SET value=excluded.value", wait)
        _db("INSERT INTO settings VALUES('llm_down_since', now()::text) ON CONFLICT(key) DO NOTHING")
        log.warning("llm: giving up status=%s model=%s strike=%s; leaving it alone for %ss. %s", r.status_code, s["model"], strikes, wait, r.text[:200])
        down = int(_db("SELECT EXTRACT(EPOCH FROM (now() - value::timestamptz)) AS d FROM settings WHERE key='llm_down_since'")[0]["d"])
        if down >= ALERT_AFTER and _db("INSERT INTO settings VALUES('llm_alerted','1') ON CONFLICT(key) DO NOTHING RETURNING key"):  # once per outage
            _tell(cfg, "Megamind: the AI provider has been failing for over an hour",
                  f"Megamind has not been able to get an answer from {s['base']} (model {s['model']}) for about {down // 60} minutes.\n"
                  f"Last response: HTTP {r.status_code}  {r.text[:300]}\n\n"
                  f"Drafts and reply handling are paused, and retries are now spaced up to {MAX_COOLDOWN // 60} minutes apart. Most often this is a used-up quota: "
                  f"check your usage with the provider, or pick another model or provider in Megamind > Settings > AI provider (saving there restarts it right away).\n"
                  f"Megamind starts again on its own when the provider answers, and will email you then.\n")
    except Exception: log.exception("llm: could not record the failure")

def _ok(cfg):
    """A call got through: forget the outage, and say so if we had emailed about it."""
    try:
        gone = {r["key"] for r in _db("DELETE FROM settings WHERE key IN ('llm_strikes','llm_down_since','llm_alerted','llm_cooldown') RETURNING key")}
        if "llm_alerted" in gone: _tell(cfg, "Megamind: the AI provider is back", "Megamind can reach the AI provider again. Drafts and replies are running normally.\n")
    except Exception: log.exception("llm: could not clear the failure state")

def _post(cfg, s, url, **k):
    if left := _cooling(): raise RuntimeError(f"AI provider is rate-limited (429/503); not retrying for another {left}s")
    for i in range(3):  # ponytail: short waits only; a quota 429 won't clear in seconds, so after the last failure we back off for minutes instead
        r = httpx.post(url, **k)
        if r.status_code not in (429, 500, 502, 503, 504): _ok(cfg); return r
        wait = 0 if i == 2 else min(_secs(r.headers.get("retry-after"), 2 ** (i + 1)), 10)
        log.warning("llm: attempt %s/3 status=%s model=%s waited %ss", i + 1, r.status_code, s["model"], wait)
        time.sleep(wait)
    _fail(cfg, s, r)
    return r

def ask(prompt, cfg=None):
    """One text-in/text-out call to whichever provider is configured."""
    cfg = cfg or config.load()
    s = settings(cfg)
    if not s: raise RuntimeError("No AI provider key set (Settings > AI provider)")
    if not s["model"]: raise RuntimeError("Set the model name in Settings > AI provider (see your provider's model list)")
    if s["fmt"] == "anthropic":
        r = _post(cfg, s, s["base"] + "/v1/messages", timeout=120, headers={"x-api-key": s["key"], "anthropic-version": "2023-06-01"},
                  json={"model": s["model"], "max_tokens": 8000, "messages": [{"role": "user", "content": prompt}]})
        return r.raise_for_status().json()["content"][0]["text"]
    r = _post(cfg, s, s["base"] + "/chat/completions", timeout=120, headers={"Authorization": "Bearer " + s["key"]},
              json={"model": s["model"], "messages": [{"role": "user", "content": prompt}]})
    return r.raise_for_status().json()["choices"][0]["message"]["content"] or ""

def json_from(text):
    return json.loads(re.search(r"\{.*\}", text, re.S).group())
