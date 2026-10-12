import os, time
import httpx

def _text(rich):
    return "".join(t["plain_text"] for t in rich)

class Notion:
    name = "notion"

    def configured(self):
        return bool(os.environ.get("NOTION_TOKEN"))

    def fetch(self):
        h = {"Authorization": f"Bearer {os.environ['NOTION_TOKEN']}", "Notion-Version": "2022-06-28"}
        with httpx.Client(base_url="https://api.notion.com/v1", headers=h, timeout=30) as c:
            cursor = None
            while True:  # search returns exactly the pages you shared with the integration = your "selection"
                body = {"filter": {"property": "object", "value": "page"}, "page_size": 50}
                if cursor: body["start_cursor"] = cursor
                r = c.post("/search", json=body).raise_for_status().json()
                for p in r["results"]:
                    if not p.get("archived"):
                        yield self._page(c, p)
                if not r["has_more"]: break
                cursor = r["next_cursor"]

    def _page(self, c, p):
        title = next((_text(v["title"]) for v in p["properties"].values() if v["type"] == "title"), "") or "Untitled"
        lines, cursor = [], None
        while True:
            time.sleep(0.35)  # Notion allows ~3 req/s
            params = {"page_size": 100, **({"start_cursor": cursor} if cursor else {})}
            r = c.get(f"/blocks/{p['id']}/children", params=params).raise_for_status().json()
            # ponytail: top-level blocks only (no toggles/columns), recurse if content goes missing
            lines += [_text(b[b["type"]]["rich_text"]) for b in r["results"] if b.get(b["type"], {}).get("rich_text")]
            if not r["has_more"]: break
            cursor = r["next_cursor"]
        return {"ext_id": p["id"], "title": title, "url": p["url"], "date": p["last_edited_time"],
                "description": "\n".join(lines)}
