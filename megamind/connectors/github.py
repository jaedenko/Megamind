import os
import httpx

class GitHub:
    name = "github"

    def configured(self):
        return bool(os.environ.get("GITHUB_TOKEN"))

    def fetch(self):
        h = {"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}", "Accept": "application/vnd.github+json"}
        with httpx.Client(base_url="https://api.github.com", headers=h, timeout=30) as c:
            # ponytail: first 100 repos only, add paging past that
            repos = c.get("/user/repos", params={"affiliation": "owner", "per_page": 100, "sort": "pushed"})
            for r in repos.raise_for_status().json():
                if r["fork"]:
                    continue
                full = r["full_name"]
                rd = c.get(f"/repos/{full}/readme", headers={"Accept": "application/vnd.github.raw+json"})
                readme = rd.text[:8000] if rd.status_code == 200 else ""
                yield {"ext_id": f"repo:{full}", "title": r["name"], "url": r["html_url"], "date": r["pushed_at"],
                       "description": f"{r['description'] or ''}\n\n{readme}".strip(),
                       "tags": [*r.get("topics", []), *filter(None, [r.get("language")])]}
                for rel in c.get(f"/repos/{full}/releases", params={"per_page": 10}).json():
                    yield {"ext_id": f"release:{rel['id']}", "title": f"{r['name']}: {rel['name'] or rel['tag_name']}",
                           "description": rel["body"] or "", "date": rel["published_at"], "url": rel["html_url"]}
                cm = c.get(f"/repos/{full}/commits", params={"per_page": 30})
                if cm.status_code == 200 and cm.json():  # 409 = empty repo
                    cm = cm.json()
                    yield {"ext_id": f"commits:{full}", "title": f"{r['name']}: recent commits", "url": f"{r['html_url']}/commits",
                           "date": cm[0]["commit"]["author"]["date"],
                           "description": "\n".join(x["commit"]["message"].split("\n")[0] for x in cm)}
