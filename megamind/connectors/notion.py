import os, time
import httpx

def _text(rich):
    return "".join(t["plain_text"] for t in rich)

class Notion:
    name = "notion"

    def __init__(self):
        self._dbs = {}

    def configured(self):
        return bool(os.environ.get("NOTION_TOKEN"))

    def fetch(self):
        self._dbs = {}
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
        par = p.get("parent") or {}
        pid = par.get(par.get("type"))  # page, database or block id (None for workspace-level pages)
        return {"ext_id": p["id"], "type": "page", "title": title, "url": p["url"], "date": p["last_edited_time"],
                "metadata": {"parent": pid if isinstance(pid, str) else None, "parent_title": self._db_title(c, pid) if par.get("type") == "database_id" else None},
                "description": "\n".join(self._blocks(c, p["id"]))}

    def _db_title(self, c, id):  # rows of a database (e.g. "Tasks") have no page parent, so ask for the database's name once
        if id not in self._dbs:
            r = c.get(f"/databases/{id}")
            self._dbs[id] = _text(r.json().get("title", [])) if r.status_code == 200 else ""
        return self._dbs[id]

    def _blocks(self, c, id):
        lines, cursor = [], None
        while True:
            time.sleep(0.35)  # Notion allows ~3 req/s
            params = {"page_size": 100, **({"start_cursor": cursor} if cursor else {})}
            r = c.get(f"/blocks/{id}/children", params=params).raise_for_status().json()
            for b in r["results"]:
                t = b.get(b["type"], {})
                if t.get("rich_text"): lines.append(_text(t["rich_text"]))
                elif b["type"] == "table_row": lines.append(" | ".join(_text(x) for x in t["cells"]))
                # child pages are synced as their own items, so don't recurse into them
                if b["has_children"] and b["type"] not in ("child_page", "child_database"):
                    lines += self._blocks(c, b["id"])
            if not r["has_more"]: break
            cursor = r["next_cursor"]
        return lines
