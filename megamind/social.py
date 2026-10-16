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
