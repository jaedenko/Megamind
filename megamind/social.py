from urllib.parse import urlencode
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
        {"response_type": "code", "client_id": client_id, "redirect_uri": redirect, "state": state, "scope": "openid profile w_member_social"})

def exchange(cfg, code, redirect):
    """Authorization code -> (access token, seconds until it expires)."""
    r = httpx.post("https://www.linkedin.com/oauth/v2/accessToken", timeout=30, data={
        "grant_type": "authorization_code", "code": code, "redirect_uri": redirect,
        "client_id": cfg("LINKEDIN_CLIENT_ID"), "client_secret": cfg("LINKEDIN_CLIENT_SECRET")})
    if r.status_code != 200: raise RuntimeError(f"LinkedIn refused the sign-in ({r.status_code}): {r.text[:200]}")
    d = r.json()
    return d["access_token"], int(d.get("expires_in", 5184000))
