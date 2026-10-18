from urllib.parse import quote, urlencode
import httpx

def _ok(r):
    if r.status_code >= 400: raise RuntimeError(f"{r.request.url.host} said {r.status_code}: {r.text[:300]}")
    return r

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

def auth_url(client_id, redirect, state):
    return "https://www.linkedin.com/oauth/v2/authorization?" + urlencode(
        {"response_type": "code", "client_id": client_id, "redirect_uri": redirect, "state": state, "scope": "openid profile w_member_social"}, quote_via=quote)

def exchange(cfg, code, redirect):
    """Authorization code -> (access token, seconds until it expires)."""
    r = httpx.post("https://www.linkedin.com/oauth/v2/accessToken", timeout=30, data={
        "grant_type": "authorization_code", "code": code, "redirect_uri": redirect,
        "client_id": cfg("LINKEDIN_CLIENT_ID"), "client_secret": cfg("LINKEDIN_CLIENT_SECRET")})
    if r.status_code != 200: raise RuntimeError(f"LinkedIn refused the sign-in ({r.status_code}): {r.text[:200]}")
    d = r.json()
    return d["access_token"], int(d.get("expires_in", 5184000))

def check_app(cfg, redirect):
    """Offers LinkedIn a deliberately wrong code to learn whether the Client ID/Secret are accepted. Returns (verdict, LinkedIn's raw words)."""
    r = httpx.post("https://www.linkedin.com/oauth/v2/accessToken", timeout=30, data={
        "grant_type": "authorization_code", "code": "megamind-check", "redirect_uri": redirect,
        "client_id": cfg("LINKEDIN_CLIENT_ID") or "", "client_secret": cfg("LINKEDIN_CLIENT_SECRET") or ""})
    raw = f"LinkedIn answered {r.status_code}: {r.text[:300]}"
    low = r.text.lower()
    if "client" in low and ("authentication" in low or "invalid_client" in low or "unauthorized" in low) or r.status_code in (401, 403):
        return "BAD KEYS: LinkedIn does not accept this Client ID / Client Secret pair. Re-copy both from the Auth tab (Primary Client Secret) and press Save.", raw
    if "redirect" in low:
        return "RETURN ADDRESS: LinkedIn is unhappy about the return address. It must be added on the Auth tab exactly as shown above.", raw
    return "The Client ID and Secret look accepted (LinkedIn only complained about the fake code, as expected).", raw
