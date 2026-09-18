# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

ApplyList — a server-rendered FastAPI app for tracking professor outreach and open positions during graduate and research applications. Deployed at http://applylist.ir/.

Four surfaces: **Professors** and **Positions** (free, per-user tracking), **Database** (paid — a catalog of faculty mirrored from public university directories), and **Assistant** (paid — Claude-backed email drafting and Q&A). Plus a full **admin panel** at `/admin`.

## Commands

```bash
docker-compose up -d db                      # Postgres on :5432
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
alembic upgrade head                         # schema
uvicorn app.main:app --reload                # http://localhost:8000
```

**Always run from the repo root.** `StaticFiles(directory="static")` and `Jinja2Templates(directory="templates")` resolve relative to the process CWD.

Tests need a real Postgres (the app uses JSONB, `date_trunc`, and `ilike`), and they drop and rebuild the `public` schema of whatever `TEST_DATABASE_URL` points at — never aim it at a database you care about:

```bash
docker-compose exec db createdb -U applylist applylist_test
pytest                                       # whole suite
pytest tests/test_billing.py -k renew        # one test
```

Migrations:

```bash
alembic revision --autogenerate -m "what changed"
alembic upgrade head
alembic downgrade -1
```

Full stack in containers: `docker-compose up --build`. The entrypoint waits for Postgres and runs `alembic upgrade head` before starting uvicorn — migrations deliberately happen there, not at app startup, so multiple workers can't race each other.

## Configuration

Everything comes from the environment via `app/config.py` (pydantic-settings); `.env.example` documents every key. Features degrade rather than crash when unconfigured — `settings.email_enabled`, `.payments_enabled`, `.ai_enabled` gate the SMTP, Zarinpal, and Claude integrations, and the admin dashboard shows which are live. With SMTP unset, verification links are written to the log instead of sent, so local signup still works.

`DATABASE_URL` accepts the `postgres://` and `postgresql://` forms hosts hand out; a validator rewrites them to `postgresql+psycopg://`.

## Architecture

FastAPI + Starlette sessions + SQLAlchemy 2.0 ORM (declarative `Mapped[...]`) + Alembic + Jinja2 + Tailwind via CDN. No frontend build step, no JS bundler, no JSON API — every route returns HTML or a redirect.

```
app/
  config.py      Settings (env-driven)
  db.py          engine, SessionLocal, session_scope(), get_db()
  deps.py        templates, render(), flash, auth dependencies
  bootstrap.py   idempotent seed: plans, universities, job titles, admin
  models/        one module per domain
  routers/       one module per surface
  services/      security, email, audit, billing, ai, scraper/
```

### Auth dependency chain

`require_user` → `require_verified` → `RequirePlan(feature)`, each building on the last, plus `require_admin`. A dependency that refuses raises `AppRedirect`, which an exception handler in `main.py` turns into a 303 plus a flash message — FastAPI dependencies can't return a redirect directly, hence the exception.

This is the one thing to get right when adding a route. **Pick the strictest dependency that fits**, because it is the only thing enforcing access:

- `Depends(require_verified)` — anything touching a user's own data
- `Depends(RequirePlan("catalog"))` / `Depends(RequirePlan("ai"))` — paid features
- `Depends(require_admin)` — already applied router-wide to `/admin`

Ownership is still checked per-row inside handlers (`if obj.owner_id != user.id`), since no dependency can know which row a path parameter refers to. Contacts and positions each have a small `_owned()` helper; use it.

### Two ways to reach the current user

`UserStateMiddleware` puts a **detached, read-only** user on `request.state.user` so `base.html` can render the nav on every page. Anything that writes must use the session-bound user from `Depends(require_user)` and friends. Writing through the middleware's object silently does nothing.

### Sessions and transactions

`get_db()` (the FastAPI dependency) and `session_scope()` (the context manager, for middleware and scripts) both **commit on clean exit** and roll back on exception. Handlers therefore mutate ORM objects and return without calling `commit()`. `expire_on_commit=False` keeps objects readable in templates after the session closes.

All mutations are POST-redirect-GET with status 303.

### Billing

Zarinpal v4, amounts in **Rial** (`Plan.price_rial`; `price_toman` is a display-only property, and the admin form takes Toman). Flow: `/billing/subscribe/{code}` creates a pending `Payment`, requests an authority, redirects to the gateway; the gateway returns the user to `/billing/callback?Authority=&Status=`, which verifies and grants.

Two things keep that callback safe, and both matter because it's a plain GET the user can refresh: `verify_payment` short-circuits on an already-paid `Payment`, and treats Zarinpal code 101 ("already verified") as success; the callback only grants a subscription when `payment.subscription_id` is still null.

`grant_subscription` extends the expiry when renewing the same plan (never costing the user days) and supersedes the old row when switching plans, so a user has at most one active subscription.

Admins can grant, extend, or revoke any plan by hand from `/admin/users/{id}` — that path works with no gateway configured at all.

### The catalog and the scraper

`CatalogProfessor` rows carry mandatory provenance: `source_url`, `source_key`, and `scraped_at`. Two flags control visibility — `is_published` (editorial) and `is_removed` (takedown). **A scrape never resurrects an `is_removed` row**, and never deletes rows that a source stopped listing, because a parser breaking after a site redesign would otherwise wipe the catalog.

`PoliteFetcher` reads and obeys `robots.txt` for our own user agent, honors `Crawl-delay`, rate-limits per host, identifies itself contactably, and caps pages per run. `SCRAPER_RESPECT_ROBOTS` exists for testing against your own fixtures — leave it on in production.

Parsers live in `services/scraper/parsers.py` and are registered in `PARSERS`. Both current ones are deliberately structure-based rather than keyed to CSS class names: `mailto_directory` anchors on `mailto:` links and walks up to the enclosing card; `profile_links` collects person-shaped anchor text. They filter out generic addresses (`admissions@`, `info@`) and non-name text. A run that succeeds with zero records almost always means the page was redesigned — `/admin/scraper/runs/{id}` says so explicitly.

Removal requests at `/database/takedown` are reachable **without an account**, deliberately: the people listed are not users, and requiring signup to opt out would be indefensible. A request unpublishes the entry immediately, before a human reviews it.

### The assistant

`services/ai.py` wraps the Anthropic SDK: `claude-opus-5`, adaptive thinking, effort from `AI_EFFORT`, streaming with `get_final_message()`. The system prompt is split into a cached block (the stable instructions) and an uncached block (a compact snapshot of the user's own contacts and positions), so the long half is a cache hit across turns.

Quota is enforced against the `AIUsage` monthly rollup **before** the API call, so an over-quota user costs nothing. The prompt forbids inventing a student's background — missing details become `[bracketed placeholders]`. Keep that property if you edit the prompt; it's the difference between a useful draft and one that lies to a professor.

### Activity logging

`services/audit.py` defines the canonical `Action` names — keep them stable, the admin filter reads them from the table. `log_activity(db, Action.X, user_id=…, request=…)` captures IP (via `X-Forwarded-For`), user agent, and path. `last_seen_at` is only rewritten every 15 minutes (`LAST_SEEN_INTERVAL`) so page views don't become a write per request.

### Templates

`base.html` holds the nav, the Tailwind CDN script, and the class-based dark-mode toggle (applied inline before first paint to avoid a flash). Every page extends it. `render()` in `deps.py` injects `request`, `user`, and the popped `flash` — use it rather than `TemplateResponse` directly, or the flash will stick around. Filters: `| dt`, `| d`, `| toman`.

## Schema changes

Alembic owns the schema. `Base.metadata.create_all()` is **not** called at startup (only the test suite uses it). Add a model, then `alembic revision --autogenerate`, then read the generated file before applying it — autogenerate misses server-default and constraint-name changes.

Startup runs `bootstrap.seed()`, which is idempotent and safe on every boot. It creates the three default plans, two universities, the reference job titles, and two scrape sources (disabled by default). `ADMIN_EMAIL` + `ADMIN_PASSWORD` create a bootstrap admin, or promote an existing account with that address — it never overwrites an existing account's password.
