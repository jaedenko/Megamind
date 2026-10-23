import json, re, time
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

COOLDOWN = 600  # seconds to leave a rate-limited provider alone

def _cooling():
    """Seconds left on the provider cooldown (0 = go ahead). Kept in the settings table so every cron tick sees it."""
    from .db import connect
    with connect() as db:
        r = db.execute("SELECT EXTRACT(EPOCH FROM (value::timestamptz - now())) AS secs_left FROM settings WHERE key='llm_cooldown'").fetchone()
    return max(0, int(r["secs_left"])) if r and r["secs_left"] else 0

def _cool_down(seconds):
    from .db import connect
    with connect() as db:
        db.execute("INSERT INTO settings VALUES('llm_cooldown', (now() + make_interval(secs => %s))::text) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (seconds,))

def _post(*a, **k):
    if left := _cooling(): raise RuntimeError(f"AI provider is rate-limited (429/503); not retrying for another {left}s")
    for i in range(3):  # ponytail: short waits only; a quota 429 won't clear in seconds, so on the last failure we back off for minutes instead
        r = httpx.post(*a, **k)
        if r.status_code not in (429, 500, 502, 503, 504): return r
        if i == 2: break
        time.sleep(min(float(r.headers.get("retry-after") or 2 ** (i + 1)), 10))
    try: wait = min(max(float(r.headers.get("retry-after") or 0), COOLDOWN), 3600)
    except ValueError: wait = COOLDOWN
    _cool_down(wait)
    return r

def ask(prompt, cfg=None):
    """One text-in/text-out call to whichever provider is configured."""
    s = settings(cfg or config.load())
    if not s: raise RuntimeError("No AI provider key set (Settings > AI provider)")
    if not s["model"]: raise RuntimeError("Set the model name in Settings > AI provider (see your provider's model list)")
    if s["fmt"] == "anthropic":
        r = _post(s["base"] + "/v1/messages", timeout=120, headers={"x-api-key": s["key"], "anthropic-version": "2023-06-01"},
                       json={"model": s["model"], "max_tokens": 8000, "messages": [{"role": "user", "content": prompt}]})
        return r.raise_for_status().json()["content"][0]["text"]
    r = _post(s["base"] + "/chat/completions", timeout=120, headers={"Authorization": "Bearer " + s["key"]},
                   json={"model": s["model"], "messages": [{"role": "user", "content": prompt}]})
    return r.raise_for_status().json()["choices"][0]["message"]["content"] or ""

def json_from(text):
    return json.loads(re.search(r"\{.*\}", text, re.S).group())
