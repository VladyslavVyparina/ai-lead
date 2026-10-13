# Previous project: bot_ai-agent

The working copy lived at `bot_ai-agent` on the author machine, about 2,400 lines of Python. This note is what that system was, and which parts the AI Lead control plane kept.

## What it was

A CRM with an AI agent for leads over Telegram, plus AI phone calls. Stack: FastAPI, SQLAlchemy on SQLite, aiogram 3, OpenAI, ElevenLabs, and Zadarma.

## Parts

| Part | Files | Role |
|---|---|---|
| Telegram bot | `app/bot.py` (452 lines) | Lead dialogue through GPT (gpt-4o-mini), voice transcription, phone capture, a typing indicator, and response cleanup |
| API and webhooks | `app/main.py` (352 lines) | FastAPI webhooks for Zadarma and for the ElevenLabs post-call payload: transcript, summary, GPT feedback, Telegram notice to the lead |
| Admin | `app/admin.py` (733 lines) plus `templates/` | Jinja2 UI: bcrypt login and sessions, lead list and detail, AI knowledge, telephony diagnostics, users and roles (owner, manager) |
| Telephony | `telephony_service.py`, `zadarma_service.py`, `elevenlabs_convai_service.py` | Start an AI call: Zadarma callback API, or ElevenLabs ConvAI over SIP, with automatic provider choice |
| Self-learning | `learning_service.py` | A `LearningProfile` of niche context appended to the system prompt, a daily worker, and a `LearningReport` |
| SIP proxy | `sip_proxy/` | Asterisk configs (pjsip, extensions, rtp) and install scripts for the Zadarma to ElevenLabs bridge |
| Landing | `site_client/` | A static card site with a SendPulse form, an email layout, and a thank-you page |

## Data

`Lead` (telegram user id, name, phone, needs, status) points at `ChatMessage` (role and source: text, voice, or call feedback) and `CallRecord` (provider, call id, transcript, summary JSON). Separate tables: `LearningProfile`, `LearningReport`, `AdminUser`, `AdminSession`, `AdminProfile`.

## What the new project reused

- FastAPI, SQLAlchemy, and Pydantic settings from `.env`.
- Webhooks that map a record once, looked up by conversation id or lead id.
- A Jinja2 operator UI when a heavy frontend is not required.
- The telephony adapter shape. AI calls are outside the first release of AI Lead, but the adapter approach carries over.

## What changed

- One bot became a deterministic orchestrator with swappable source adapters and bounded agents.
- Commerce, scoring, and messaging are versioned configuration, not code and not prompts.
- The lead state machine (`new` through `won`, `lost`, or `suppressed`) writes an immutable event log.
- Outreach waits for a person, plus consent and suppression checks.
- Preview sites are a factory (SiteConfig, a Next.js template, deploy) instead of a chat transcript.
