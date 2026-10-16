import base64, hashlib, hmac, secrets, time
from urllib.parse import quote
import httpx

def _ok(r):
    if r.status_code >= 400: raise RuntimeError(f"{r.request.url.host} said {r.status_code}: {r.text[:300]}")
    return r

def oauth1(method, url, ck, cs, at, ats, extra=None, nonce=None, ts=None):
    """OAuth 1.0a HMAC-SHA1 header (what X's v2 'create post' accepts with your four keys). JSON bodies are not signed, only `extra` form params."""
    p = {"oauth_consumer_key": ck, "oauth_nonce": nonce or secrets.token_hex(16), "oauth_signature_method": "HMAC-SHA1",
         "oauth_timestamp": ts or str(int(time.time())), "oauth_token": at, "oauth_version": "1.0"}
    enc = lambda s: quote(str(s), safe="~")
    allp = sorted((enc(k), enc(v)) for k, v in {**p, **(extra or {})}.items())
    base = "&".join([method.upper(), enc(url), enc("&".join(f"{k}={v}" for k, v in allp))])
    p["oauth_signature"] = base64.b64encode(hmac.new(f"{enc(cs)}&{enc(ats)}".encode(), base.encode(), hashlib.sha1).digest()).decode()
    return "OAuth " + ", ".join(f'{enc(k)}="{enc(v)}"' for k, v in sorted(p.items()))

def x_ready(cfg):
    return all(cfg(k) for k in ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET"))

def post_x(cfg, text):
    url = "https://api.x.com/2/tweets"
    h = oauth1("POST", url, cfg("X_API_KEY"), cfg("X_API_SECRET"), cfg("X_ACCESS_TOKEN"), cfg("X_ACCESS_SECRET"))
    d = _ok(httpx.post(url, headers={"Authorization": h}, json={"text": text}, timeout=30)).json()
    return "https://x.com/i/web/status/" + d["data"]["id"]

def linkedin_ready(cfg):
    return bool(cfg("LINKEDIN_ACCESS_TOKEN"))

def _esc(text):  # LinkedIn "little text": these characters are markup unless escaped
    return "".join("\\" + c if c in "\\|{}@[]()<>#*_~" else c for c in text)

def post_linkedin(cfg, text):
    h = {"Authorization": "Bearer " + cfg("LINKEDIN_ACCESS_TOKEN")}
    sub = _ok(httpx.get("https://api.linkedin.com/v2/userinfo", headers=h, timeout=30)).json()["sub"]
    r = _ok(httpx.post("https://api.linkedin.com/rest/posts", timeout=30,
                       headers={**h, "LinkedIn-Version": cfg("LINKEDIN_VERSION", "202608"), "X-Restli-Protocol-Version": "2.0.0"},
                       json={"author": "urn:li:person:" + sub, "commentary": _esc(text), "visibility": "PUBLIC", "lifecycleState": "PUBLISHED",
                             "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [], "thirdPartyDistributionChannels": []},
                             "isReshareDisabledByAuthor": False}))
    return "https://www.linkedin.com/feed/update/" + r.headers["x-restli-id"] + "/"
