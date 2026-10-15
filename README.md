# ProjectPost (repo: Megamind)

Turns your projects into LinkedIn/X posts and keeps everything you've made in one searchable database. Runs entirely on Vercel; you only use a browser. Built in 7 stages; **stages 1-2 are done**: Postgres, GitHub/Notion/website connectors, a sync page, and AI grouping of items into projects.

## Environment variables
Set every one in **Vercel dashboard > your project > Settings > Environment Variables**, then **Deployments > ... > Redeploy** so they take effect. Nothing secret lives in the code.

| Variable | Required | Where to get it |
|---|---|---|
| `DATABASE_URL` | yes | Vercel project > **Storage** > Create Database > **Neon** (Postgres). Connecting it to the project sets `DATABASE_URL` for you. (`POSTGRES_URL` also works.) |
| `APP_PASSWORD` | yes | Make one up. The browser asks for it when you open the site (username can be anything). Without it the site refuses to load. |
| `GITHUB_TOKEN` | for GitHub | github.com/settings/tokens > fine-grained token, "All repositories" (or pick some), read-only for Contents, Metadata and Issues/Releases. A classic token with `repo` scope also works. |
| `NOTION_TOKEN` | for Notion | notion.so/profile/integrations > new internal integration > copy the secret. Then on each page or database to pull: `...` menu > Connections > add your integration. Only shared pages sync, with all nested text; sub-pages sync as their own items. |
| `SITE_URL`, `SITE1_URL`, `SITE2_URL`... | for website | Site roots, e.g. `https://example.com`; any `SITE<n>_URL` is read. Reads `/sitemap.xml`, falls back to `/feed.xml` or `SITE_FEED`. |
| `SITE_FEED` | optional | Full URL of an RSS feed if it isn't `/feed.xml`. |
| `SITE_MAX_PAGES` | optional | Max website pages per sync (default 50). |
| `ANTHROPIC_API_KEY` | for grouping | console.anthropic.com > API keys. Used by the **Group items into projects** button. |
| `CLUSTER_MODEL` | optional | Model id for grouping (default `claude-sonnet-5-5`). |
| `MEGAMIND_DISABLE` | optional | Comma list of sources to switch off, e.g. `notion,website`. |

Leave a source's variable unset to skip it. Variables for later stages (Voyage, Anthropic, email, LinkedIn, X, `CRON_SECRET`) will be added to this table as those stages ship.

## Deploy
Push to GitHub; Vercel builds and deploys automatically. The tables are created automatically on the first request.

## Using it
Open your Vercel URL, enter the password, click **Re-sync** next to a source. Re-syncing never duplicates items and never overwrites items you've edited (`edited = true`) or their project. A sync runs inside one request, so it is capped at 5 minutes; if it hits the cap, the items saved so far are kept (the run just won't show as finished).

## Tests (for development)
`DATABASE_URL=<throwaway postgres> python test_stage1.py` (wipes that database's `public` schema).

## Adding a source
Add `megamind/connectors/yours.py` with `name`, `configured()` and `fetch()`, yielding `{ext_id, title, [type, description, date, url, media[], tags[], metadata{}]}`, then add it to the list in `megamind/connectors/__init__.py`. No schema change needed.

## Status / handoff notes
- **Done: stages 1-2.** Deployed to Vercel project `megamind` (team `jajaphet02-8025`), auto-deploys on push to `main`. Live URL: https://megamind-jajaphet02-8025.vercel.app/
- Stage 2 is deliberately lazy: one Claude call groups unassigned items into projects (`megamind/cluster.py`), search stays substring, no pgvector/Voyage. It only fills items with no project, so manual moves (dropdown on each item) and renames stick. Upgrade to embeddings when items pass ~1000 or grouping gets noisy.
- **Next: stage 3** (post studio: pick a project + angle, Claude drafts LinkedIn and X versions into `posts`). Then email approval + Vercel Cron, chat panel, Onshape/imports, publishing.
- Stack: Python/FastAPI single Vercel function (`pyproject.toml` entrypoint `megamind.main:app`, no `api/` dir, no `requirements.txt`). Schema is created lazily in `megamind/db.py`; add the `embedding vector` column there in stage 2 (`CREATE EXTENSION vector`).
- Vercel has Deployment Protection (Vercel login) on in addition to `APP_PASSWORD`; tools that fetch the site anonymously get a 401 from Vercel.
- Local test DB recipe (no Docker): `initdb` + `pg_ctl` from `/usr/lib/postgresql/16/bin`, then `DATABASE_URL=... python test_stage1.py`.
