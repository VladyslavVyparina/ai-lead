# AI Lead

A control plane that turns raw business records into verified leads, a preview website, a priced proposal, and an outreach draft a human still has to approve.

## Problem

Agencies were assembling first-touch packages by hand: find a local business, check that it is real, write a pitch, and mock a site. The same steps were repeated for every niche and city, and nothing stopped a message from going out on a weak or duplicate lead.

## What was built

A deterministic orchestrator, not a free-form chatbot. It imports or discovers businesses, deduplicates them, attaches evidence, scores fit, and only then prepares a preview and an outreach draft. A person approves the preview and approves the send. The pilot market is beauty and wellness in Porto, Lisbon, and Braga: 15 Tier A and 15 Tier B packages, 30 controlled contacts, with a target of 5 qualified conversations.

## How it works

1. A connector or CSV import creates source records.
2. Names and phones are normalized and duplicates are merged.
3. Enrichment marks each fact as confirmed, inferred, or missing.
4. FitQL scores the lead (weights 30/25/20/15/10, thresholds 75 and 55) and assigns Tier A or B.
5. The operator approves a preview. The model fills a content pack (hero, services, call to action, contact). A Next.js template renders it. The app itself is not rewritten per lead.
6. A proposal is priced from a versioned price book.
7. Outreach is blocked until approval, consent, and suppression checks pass. A lead gets at most four touches.

The operator UI is at `http://localhost:8000/ui` (token from `ADMIN_TOKEN`). Swagger is at `/docs`. Lead state changes are guarded, and every transition is written to an event log. Config for markets, scoring, price book, and messaging is versioned YAML with publish and rollback.

## Stack

Python, FastAPI, SQLite, Next.js preview template, optional OpenAI for the content pack (there is a deterministic fallback), Vercel or a local stub for hosting.

## Run locally

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
.venv/bin/python run_api.py
.venv/bin/python -m pytest
```

## What this public copy leaves out

Secrets, the local pipeline database, virtualenv, and the bundled Node toolchain. The working copy stays on the author machine.

## Documents

- `docs/TESTING.md` is the operator checklist: pytest, the UI path from import to an approved send, the local preview on port 3100, and CORS.
- `docs/BOT_AI_AGENT_SUMMARY.md` is the earlier Telegram-and-calls CRM this control plane replaced, and which pieces were kept.
- `site_template/README.md` is the Next.js preview. One lead changes `SiteConfig` only. Missing facts are not rendered, and prices are not invented.
- Markets, FitQL weights, the price book, and message copy are versioned YAML with publish and rollback. The pilot is 15 Tier A and 15 Tier B packages in Porto, Lisbon, and Braga.
