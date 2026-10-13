# wellness-next-template

A category Next.js template for a preview site (massage and wellness, plus beauty). It renders a validated `SiteConfig`. The contract lives in the backend at `app/core/schemas.py` (`SiteConfig`). The template registry is `config/templates/v1.yaml`, id `wellness-next-template`.

## Rules

- A routine build for one lead changes only `SiteConfig`. The template code is not edited per lead.
- The page is `force-dynamic`. `SITE_CONFIG` is read on the request, not during `npm run build`. A build-time read would show the same demo to every lead.
- `next.config` does not put `SITE_CONFIG` in `env`. That would bake one value into the build.
- A missing fact means the section is not rendered. Nothing is invented. Unverified prices are not shown.
- Preview mode shows a “conceito” banner, `noindex`, and posts the form to the backend `POST /preview-form`, not to the business.
- Per-lead fields: name, description, services and prices, contacts (click-to-call, WhatsApp, mailto), address and hours, brand color, SEO.

## Run locally

```bash
cd site_template
npm install
npm run dev
```

That serves `http://localhost:3000` from `sample-config.json`. The form will not work: the token in the sample is fake.

For a working form, seed from the backend. See `docs/TESTING.md`.

```bash
# from the repo root, with uvicorn on :8000
.venv/bin/python scripts/seed_local_preview.py
cd site_template
SITE_CONFIG="$(cat .local-site-config.json)" npm run dev -- --port 3100
```

Production mode:

```bash
SITE_CONFIG="$(cat my-lead-config.json)" npm run build
SITE_CONFIG="$(cat my-lead-config.json)" npm start
```

The build can run without `SITE_CONFIG`. The process that serves the page cannot.

## Deploy

The backend hosting adapter (`app/adapters/hosting.py`, Vercel) passes `SITE_CONFIG` as an environment variable at deploy time. To turn on a real deploy, publish this folder as a Vercel project and set `VERCEL_TOKEN` in the backend `.env`.
