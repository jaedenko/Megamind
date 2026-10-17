import os, re
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from xml.etree import ElementTree as ET
import httpx

class _Text(HTMLParser):
    SKIP = {"script", "style", "nav", "footer", "header", "noscript", "svg"}
    def __init__(s):
        super().__init__(); s.skip, s.parts, s.title, s.img, s.in_title = 0, [], "", None, False
    def handle_starttag(s, tag, a):
        a = dict(a)
        if tag in s.SKIP: s.skip += 1
        elif tag == "title": s.in_title = True
        elif tag == "meta" and a.get("property") == "og:image": s.img = a.get("content")
    def handle_endtag(s, tag):
        if tag in s.SKIP: s.skip = max(0, s.skip - 1)
        elif tag == "title": s.in_title = False
    def handle_data(s, d):
        if s.in_title: s.title += d
        elif not s.skip and d.strip(): s.parts.append(d.strip())

def extract(html):
    p = _Text(); p.feed(html)
    return p.title.strip(), "\n".join(p.parts)[:8000], p.img

def _kids(e):
    return {k.tag.rsplit("}", 1)[-1]: (k.text or "").strip() for k in e}

def _rows(xml):
    root = ET.fromstring(xml)
    for e in root.iter():
        tag = e.tag.rsplit("}", 1)[-1]
        if tag == "url":  # ponytail: plain sitemap only, add sitemap-index recursion if yours is one
            k = _kids(e); yield k.get("loc"), k.get("lastmod")
        elif tag == "item":
            k = _kids(e)
            try: d = parsedate_to_datetime(k["pubDate"]).isoformat()
            except Exception: d = None
            yield k.get("link"), d

class Website:
    name = "website"

    def configured(self):
        return bool(self._bases())

    @staticmethod
    def env_bases():  # SITE_URL, SITE1_URL, SITE2_URL, ... set in Vercel
        return [v.rstrip("/") for k, v in sorted(os.environ.items()) if re.fullmatch(r"SITE\d*_URL", k) and v]

    def _bases(self):  # plus the sites you added in the web page
        from .. import config
        return list(dict.fromkeys(self.env_bases() + config.sites()))

    def _rows_for(self, c, base):
        for feed in (base + "/sitemap.xml", os.environ.get("SITE_FEED") or base + "/feed.xml"):
            r = c.get(feed)
            if r.status_code != 200: continue
            try: root = ET.fromstring(r.content)
            except ET.ParseError: continue
            if root.tag.rsplit("}", 1)[-1] == "sitemapindex":  # many site builders split the sitemap into several files
                rows = []
                for loc in [(e.text or "").strip() for e in root.iter() if e.tag.rsplit("}", 1)[-1] == "loc"][:10]:
                    child = c.get(loc)
                    if child.status_code == 200: rows += [x for x in _rows(child.content) if x[0]]
            else:
                rows = [x for x in _rows(r.content) if x[0]]
            if rows: return rows
        return [(base, None)]  # one-page sites (Carrd and friends) have no sitemap: read the page itself

    def fetch(self):
        cap, errors = int(os.environ.get("SITE_MAX_PAGES", 50)), []
        with httpx.Client(follow_redirects=True, timeout=30) as c:
            for base in self._bases():
                try:
                    for url, date in self._rows_for(c, base)[:cap]:
                        r = c.get(url)
                        if r.status_code != 200 or "html" not in r.headers.get("content-type", ""):
                            continue
                        title, text, img = extract(r.text)
                        yield {"ext_id": url, "type": "page", "title": title or url, "description": text, "date": date, "url": url,
                               "media": [img] if img else []}
                except Exception as e:  # keep going: one unreachable site must not hide the others
                    errors.append(f"{base}: {e!r}"[:200])
        if errors: raise RuntimeError("; ".join(errors))
