import json, re
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

def ask(prompt, cfg=None):
    """One text-in/text-out call to whichever provider is configured."""
    s = settings(cfg or config.load())
    if not s: raise RuntimeError("No AI provider key set (Settings > AI provider)")
    if not s["model"]: raise RuntimeError("Set the model name in Settings > AI provider (see your provider's model list)")
    if s["fmt"] == "anthropic":
        r = httpx.post(s["base"] + "/v1/messages", timeout=120, headers={"x-api-key": s["key"], "anthropic-version": "2023-06-01"},
                       json={"model": s["model"], "max_tokens": 8000, "messages": [{"role": "user", "content": prompt}]})
        return r.raise_for_status().json()["content"][0]["text"]
    r = httpx.post(s["base"] + "/chat/completions", timeout=120, headers={"Authorization": "Bearer " + s["key"]},
                   json={"model": s["model"], "messages": [{"role": "user", "content": prompt}]})
    return r.raise_for_status().json()["choices"][0]["message"]["content"] or ""

def json_from(text):
    return json.loads(re.search(r"\{.*\}", text, re.S).group())
