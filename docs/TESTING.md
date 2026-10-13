# How to test AI Lead Pipeline

Three layers: automated tests, the backend and operator UI, and the preview site.

## 0. One-time setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp -n .env.example .env
```

`.env` needs a real `ADMIN_TOKEN` and `INBOUND_WEBHOOK_SECRET`. The site template also needs Node:

```bash
cd site_template
npm install
```

## 1. Automated tests, before any deploy

```bash
.venv/bin/python -m pytest -q
```

The source tree expected 57 or more passing tests. Backend and site build together:

```bash
bash scripts/test_all.sh
```

## 2. Backend and operator UI

```bash
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
```

| URL | What it is |
|---|---|
| `http://127.0.0.1:8000/ui` | Operator UI. The login is `ADMIN_TOKEN`. |
| `http://127.0.0.1:8000/docs` | Swagger. |
| `http://127.0.0.1:8000/health` | `{"ok":true}` |

Short path through the UI:

1. Sources: create a `csv_import` connector and add a business by hand, with a link to the source.
2. Leads: open the card, verify contacts, save the audit (clear “Has a website” when there is none), add facts (`rating`, `review_count`, `address`), then score.
3. Move the state to `shortlisted`, build the preview site, then create the proposal.
4. State `approved`, then outreach: draft, approve, and mark sent only after a real message goes out.

## 3. Preview site locally (form, backend, dashboard)

`sample-config.json` has a fake `form_token`. Generate a live seed first:

```bash
# terminal 1 — backend, same pipeline.db
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000

# terminal 2 — writes site_template/.local-site-config.json with a live token
.venv/bin/python scripts/seed_local_preview.py

# terminal 3 — site on that config
cd site_template
SITE_CONFIG="$(cat .local-site-config.json)" npm run dev -- --port 3100
```

Open `http://127.0.0.1:3100`. The sample title is “Estúdio Local Preview”. Submit the booking form. The lead appears on `/ui` of the same backend.

The form posts to whatever `form_endpoint` is in the config. A preview on `localhost` and a dashboard on another host are different databases. Read the dashboard on the host that received the POST.

Restart uvicorn after a change to `.env` or `app/config.py`, or CORS stays on the old value.

Smoke check without a browser:

```bash
.venv/bin/python scripts/smoke_test.py \
  --base-url http://127.0.0.1:8000 \
  --admin-token "$ADMIN_TOKEN" \
  --preview-origin http://127.0.0.1:3100
```

## 4. Checklist

Backend and UI:

- [ ] `/health` returns ok
- [ ] A wrong token does not open the UI
- [ ] CSV import and quick-add create leads
- [ ] A preview build is blocked until contacts are verified
- [ ] After scoring, the state is `fitql` or `rejected`
- [ ] Suppression blocks approval of the next message

Preview site:

- [ ] “conceito” banner and `noindex` in preview mode
- [ ] Missing facts are omitted. Prices are not invented.
- [ ] Click-to-call, WhatsApp, and mailto work
- [ ] The form posts to `PUBLIC_BASE_URL/preview-form` and the row shows on the dashboard
- [ ] A different `SITE_CONFIG` changes the page (`force-dynamic`, not a static bake)

CORS:

- A form from `http://localhost:3100`, `http://127.0.0.1:3100`, or `https://*.vercel.app` must pass OPTIONS and POST
- If `.env` still has an old `PREVIEW_ORIGIN_REGEX` without `127.0.0.1`, comment that line out and use the regex derived from `PUBLIC_BASE_URL`
- A foreign Origin gets no `Access-Control-Allow-Origin`

## 5. On the server after a code update

```bash
pkill -f "uvicorn app.main:app" || true
.venv/bin/python -m pytest -q
nohup .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 > app.log 2>&1 &
curl -s http://localhost:8000/health
```

Drop `pipeline.db` only when the database is a disposable test and the schema changed. A real Vercel deploy of `site_template/` is a separate step and needs `VERCEL_TOKEN` in `.env`.
