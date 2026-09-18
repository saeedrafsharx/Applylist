# ApplyList · Application tracker for grad school and research jobs

> Track professors, labs, and open positions while you apply for **Master's/PhD**, research posts,
> and academic jobs — instead of a spreadsheet that falls apart in week three.

🌐 **Live:** [applylist.ir](http://applylist.ir/) · 🧑‍💻 **MIT licensed**

---

## What it does

**Free, for everyone:**
- 📇 **Contact cards** — name, university, research focus, email, source link, notes
- ✉️ **Email + reminder status** — one click, timestamped
- 🎯 **Positions** — PhD calls, fellowships, RA posts, with deadlines and an application status
- 📤 **CSV import/export** — bring your existing spreadsheet, take your data with you
- 🔒 **Private per account**, with email-verified signup

**Pro:**
- 🗂️ **Supervisor database** — faculty entries mirrored from universities' own public directories, searchable by research area, savable straight into your list
- ✨ **AI assistant** — drafts cold emails and follow-ups against the contacts you already track, reviews your own drafts, answers questions about the process. English or Persian. Runs on any OpenAI-compatible provider.

**For the operator:**
- 📊 **Admin panel** — users, subscriptions, payments, activity log, catalog editing, scraper control, takedown queue

---

## Running it

### Docker (everything)

```bash
cp .env.example .env    # then edit it — at minimum SECRET_KEY and DATABASE_URL
docker-compose up --build
```

Open <http://localhost:8000>. The entrypoint waits for Postgres and applies migrations before serving.

### Local development

```bash
docker-compose up -d db          # just Postgres
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
alembic upgrade head
uvicorn app.main:app --reload
```

Run from the repo root — template and static paths are relative to the working directory.

With `SMTP_HOST` unset, verification emails are written to the application log instead of sent, so you can sign up locally without a mail server. Look for the `/verify-email/<token>` link in the console.

### Tests

```bash
docker-compose exec db createdb -U applylist applylist_test
pytest
```

The suite needs a real Postgres (the app uses JSONB, `date_trunc`, `ilike`) and **drops and recreates the `public` schema** of whatever `TEST_DATABASE_URL` points at. Never aim it at a database you care about. No network is used — SMTP, Zarinpal, and the model provider are all disabled in the fixtures.

---

## Configuration

Every setting is an environment variable; `.env.example` lists them all with comments. The ones that matter:

| Variable | Why it matters |
|---|---|
| `SECRET_KEY` | Signs session cookies. **Leaving the default in production means anyone can forge a session.** |
| `DATABASE_URL` | Postgres. `postgres://` and `postgresql://` are accepted and rewritten. |
| `BASE_URL` | Used to build verification and payment-callback links. Must be the real public URL. |
| `SMTP_*` | Without these, signup verification emails are logged, not delivered. |
| `ZARINPAL_MERCHANT_ID` | Enables online payment. Keep `ZARINPAL_SANDBOX=true` until you've tested the full loop. |
| `OPENAI_API_KEY` | Enables the AI assistant. |
| `OPENAI_BASE_URL` | Point at any OpenAI-compatible provider. Blank = `api.openai.com`, which is **not reachable from Iran** — set this if the server is hosted there. |
| `OPENAI_MODEL` | Default `gpt-4o-mini`. Raise it if draft quality matters more than cost. |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | Creates or promotes the first admin on boot. |

Each integration degrades on its own: an unset key disables that feature and says so on the admin dashboard, rather than breaking the app.

---

## Going to production

1. **Set `SECRET_KEY`** to something random — `python -c "import secrets; print(secrets.token_urlsafe(48))"`. Do not skip this.
2. **Set `ENVIRONMENT=production`.** This turns on `https_only` session cookies and hides `/api/docs`.
3. **Set `BASE_URL`** to your real HTTPS URL, or verification and payment callbacks will point at localhost.
4. **Configure SMTP** — without it nobody can confirm their email, and verification gates every dashboard.
5. **Test Zarinpal in sandbox first**, all the way through: subscribe → gateway → callback → subscription active.
6. **Back up Postgres.** Nothing in here backs itself up.
7. **Set `ADMIN_EMAIL` / `ADMIN_PASSWORD`**, sign in, then remove `ADMIN_PASSWORD` from the environment.

---

## The catalog, and how it is collected

The supervisor database mirrors faculty directory pages that universities publish openly. The crawler:

- identifies itself with a contactable `User-Agent`
- reads and obeys `robots.txt`, including `Crawl-delay`
- rate-limits per host and caps pages per run
- records the **source URL and timestamp on every entry**, so any row can be traced back and re-checked
- is triggered by hand from the admin panel, per source — nothing crawls on a schedule
- skips generic addresses (`admissions@`, `info@`) — those are not people

**Removal:** anyone listed can ask to be taken off at `/database/takedown` **without creating an account**. The entry is unpublished immediately, before a human reviews it, and a later scrape will not bring it back.

If you deploy this and charge for catalog access, the legal position on republishing personal contact data — GDPR in the EU, PIPEDA in Canada — is yours to get right. The provenance tracking and takedown flow exist to make that possible, not to settle it.

---

## Stack

FastAPI · SQLAlchemy 2.0 · Alembic · PostgreSQL · Jinja2 · Tailwind (CDN) · Zarinpal · any OpenAI-compatible model provider

No frontend build step. Server-rendered HTML throughout.

---

## Contributing

- Translate the UI (Farsi especially welcome)
- Add scraper parsers for more university directories
- University-specific email templates
- Open an issue with ideas or bugs

If it helps your applications, ⭐ the repo so other students find it.

---

## License

MIT. Built by students, for students. Good luck with your applications. 🎓
