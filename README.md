# ProjectPost (repo: Megamind)

Turns your projects into LinkedIn/X posts. Built in 6 stages; stage 1 is done: it pulls your projects from GitHub, Notion and your website into one SQLite DB, with a sync page at `/`.
Tables: `items`, `projects`, `posts`, `corrections`, `settings`, `sync_runs` (posts/corrections/settings are filled by later stages).

## Setup
```
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env     # fill in the keys you have; blank = skipped
```

## Run
```
python -m megamind.sync            # all configured sources
python -m megamind.sync github     # just one
uvicorn megamind.main:app --reload # open http://127.0.0.1:8000  (API: GET /items, /projects, /sync, POST /sync[/{source}], docs at /docs)
python test_stage1.py              # self-check
```
Disable a source without touching code: `MEGAMIND_DISABLE=notion,website` in `.env`.
Re-syncing never duplicates items and never overwrites items you've edited (`edited=1`) or their project.

## Credentials
- **GitHub** `GITHUB_TOKEN`: github.com/settings/tokens, fine-grained token, "All repositories" (or pick some), read-only for Contents, Metadata and Issues/Releases. Classic token with `repo` scope also works.
- **Notion** `NOTION_TOKEN`: notion.so/profile/integrations, new internal integration, copy the secret. Then on each page or database you want pulled: `...` menu, Connections, add your integration. Only shared pages are synced, with all nested text (toggles, columns, tables); sub-pages sync as their own items.
- **Website** `SITE_URL`: your site root, e.g. `https://example.com`. Uses `/sitemap.xml`, falls back to `/feed.xml` or `SITE_FEED`.
