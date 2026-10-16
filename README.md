# Megamind

Turns your projects into LinkedIn/X posts (it posts to LinkedIn; you paste the X one) and keeps everything you've made in one searchable database. Runs entirely on Vercel; you only use a browser. Built in 7 stages; **stages 1-5 are done**: Postgres, GitHub/Notion/website connectors, search, grouping into projects, scheduled drafts, email approval, and posting to LinkedIn (the X post is emailed to you to paste).

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
| `CRON_SECRET` | optional | Only for Vercel's own daily cron: any long random string (Vercel sends it automatically). Not needed if you use the private check link from Settings > Automatic checking. |

**Settings box (in the web page, no redeploy needed).** The AI provider, email, X and LinkedIn values below can be typed into the page's **Settings** section instead of Vercel. A saved value overrides an env var of the same name; secrets are stored in your database and never shown again.

| Setting | Notes |
|---|---|
| `LLM_PROVIDER`, `LLM_API_KEY`, `LLM_MODEL` | Any provider: openai, anthropic, openrouter, groq, together, deepseek, mistral, xai, gemini, opencode, opencode-go, or `custom` plus `LLM_BASE_URL` for any OpenAI-compatible server. The older `OPENCODE_API_KEY` / `ANTHROPIC_API_KEY` env vars still work. |
| `MAIL_USER`, `MAIL_PASSWORD`, `MAIL_TO` | The mailbox Megamind sends drafts from and reads replies in. Gmail: turn on 2-step verification, then Google Account > Security > App passwords. `MAIL_TO` is you (defaults to `MAIL_USER`, i.e. you email yourself). Optional `SMTP_HOST` / `IMAP_HOST` for non-Gmail. |
| `LINKEDIN_ACCESS_TOKEN` | linkedin.com/developers > your app > Products: add "Share on LinkedIn" and "Sign In with LinkedIn using OpenID Connect", then Auth > OAuth token generator with scopes `openid profile w_member_social`. Tokens last about 60 days; paste the new one in Settings when it expires. Optional `LINKEDIN_VERSION` (YYYYMM, default 202608). |

| `VOYAGE_API_KEY` | optional | dash.voyageai.com. Meaning-based search, plus a no-LLM way to group items. Needs the pgvector extension (Neon has it). |
| `MEGAMIND_DISABLE` | optional | Comma list of sources to switch off, e.g. `notion,website`. |

Leave a source's variable unset to skip it. Variables for later stages (email, LinkedIn, X, `CRON_SECRET`) will be added to this table as those stages ship.

## Deploy
Push to GitHub; Vercel builds and deploys automatically. The tables are created automatically on the first request.

## Using it
Open your Vercel URL, enter the password, click **Re-sync** next to a source. Re-syncing never duplicates items and never overwrites items you've edited (`edited = true`) or their project. A sync runs inside one request, so it is capped at 5 minutes; if it hits the cap, the items saved so far are kept (the run just won't show as finished).

- **Group items into projects**: with an LLM key, a model names and summarises projects; otherwise (Voyage only) items are matched to the nearest project and projects are named after their first item. Either way it only fills items that have no project yet, so manual moves (dropdown on each item) and renames stick.
- **Ignore / Restore**: every item has an Ignore button; **Show ignored** lists them with Restore. Ignored items disappear from search, grouping and embeddings and are never re-added by a sync. Ignoring a Notion page also ignores everything nested under it (and restoring brings it back).
- **Notion ignore list**: pages titled `Tasks` or `College packing list` (and everything under them, including rows of a database with that name) are ignored automatically after each sync. The list lives in the `settings` table (`key = 'ignore_titles'`, titles separated by `|`); change it there. It is applied once per new item, so your Restore/Ignore clicks always win.

## Scheduled posts and email approval
1. In **Posts**, schedule a post (pick a project or auto-pick, a date/time, optional repeat every N days), or click **Draft and email me now**.
2. When it is due, Megamind writes a LinkedIn post and a short X post and emails both to you.
3. Reply to that email: **Y** (or a thumbs-up, alone) posts the LinkedIn post and emails you the X text to copy and paste; **N** asks for a fresh version; **anything else** is feedback, or paste a complete replacement and it is used. Each reply produces a new email. This repeats until you reply Y. Nothing is ever posted without a bare Y; "yes but shorten it" counts as feedback.
4. Replies are only accepted from `MAIL_TO`, and only if they carry the secret tag in the subject line (keep the subject when replying).
5. Megamind never posts to X: there is no X API key to set up. If LinkedIn rejects a post (usually an expired token) you get one email and it retries on every check until you paste a new token in Settings.

**The page.** Four tabs: *Start here* (setup checklist), *My work* (sources, projects, items), *Posts* (write now, schedule, approvals) and *Settings* (each integration has a Save button, a Test button where a free test exists, and step-by-step "Where do I get these?" help).

**How often does it check?** `/cron/tick` does the work: due schedules, new replies, retries. `vercel.json` runs it once a day, because Vercel Hobby rejects anything more frequent at deploy time. That means a reply can wait up to a day. For quick turnaround: on Vercel Pro change the schedule in `vercel.json` to `*/5 * * * *`; on Hobby use a free external pinger (e.g. cron-job.org, every 5 minutes) pointed at the private check link shown in Settings > Automatic checking (`/cron/tick?key=...`, made automatically; it opens only that one endpoint). If Vercel Authentication blocks that request, either add the project's Protection Bypass for Automation header (`x-vercel-protection-bypass`) or turn Vercel Authentication off for production (the site is already behind `APP_PASSWORD`). The **Check now** button runs the same thing on demand.

## Tests (for development)
`DATABASE_URL=<throwaway postgres> python test_stage1.py` (wipes that database's `public` schema). One test needs pgvector and is skipped on a plain Postgres.

## Adding a source
Add `megamind/connectors/yours.py` with `name`, `configured()` and `fetch()`, yielding `{ext_id, title, [type, description, date, url, media[], tags[], metadata{}]}`, then add it to the list in `megamind/connectors/__init__.py`. No schema change needed.

## Status / handoff notes
- **Done: stages 1-5** (3 post studio, 4 email approval loop + scheduling, 5 posting). Deployed to Vercel project `megamind` (team `jajaphet02-8025`), auto-deploys on push to `main`. Live URL: https://megamind-jajaphet02-8025.vercel.app/
- Stage 2 = Voyage embeddings + hybrid search (`megamind/embed.py`, optional) + LLM grouping (`megamind/cluster.py`, optional). `POST /cluster` prefers the LLM, falls back to embeddings if the model call fails or no LLM key is set.
- **Next:** chat panel, Onshape/Fusion/Instagram imports, image attachments for posts, LinkedIn token auto-refresh (needs an OAuth flow).
- Stack: Python/FastAPI single Vercel function (`pyproject.toml` entrypoint `megamind.main:app`, no `api/` dir, no `requirements.txt`). Schema is created lazily in `megamind/db.py`.
- Vercel has Deployment Protection (Vercel login) on in addition to `APP_PASSWORD`; tools that fetch the site anonymously get a 401 from Vercel.
- Local test DB recipe (no Docker): `pip install pgserver` (bundles Postgres + pgvector), or `initdb` + `pg_ctl` from `/usr/lib/postgresql/16/bin`, then `DATABASE_URL=... python test_stage1.py`.
