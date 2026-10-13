# AI Lead

Платформа, яка з публічних даних про бізнес збирає ліди, кваліфікує їх і готує контрольований пакет для першого контакту. Пілот був для beauty і wellness у Порту, Лісабоні та Бразі.

**Стек.** Python, FastAPI, SQLite.

Публічний кейс. Робоча копія з паролями і базами лишається локально і сюди не входить.

---

# AI Lead Pipeline

Скелет платформи за архітектурою **AI Lead-to-Website Pipeline** (див. `docs/ARCHITECTURE_SOURCE.txt`): детермінований оркестратор, що перетворює знайдені або вхідні бізнеси на верифіковані ліди, персоналізовані превʼю-сайти, пропозиції та контрольований аутріч.

Пілот: beauty/wellness у Porto, Lisbon, Braga; 15 Tier A + 15 Tier B пакетів; 30 контрольованих контактів; ціль — 5 кваліфікованих розмов.

## Швидкий старт

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env        # заповнити секрети
.venv/bin/python run_api.py # запуск
.venv/bin/python -m pytest  # тести
```

Після запуску:

- **Операторський UI: `http://localhost:8000/ui`** — вхід за `ADMIN_TOKEN`; весь цикл (джерела → ліди → превʼю/пропозиції → аутріч) через браузер, без технічних навичок.
- **Vertical slice:** на сторінці «Джерела» натисніть **Run connector (20 leads)** — discovery → enrichment → FitQL → top 5 shortlist. Далі лише **Approve preview** на картці ліда та **Затвердити** аутріч-чернетку.
- API/Swagger: `http://localhost:8000/docs` — для автоматизації та інтеграцій.
- Повна інструкція з тестування: [`docs/TESTING.md`](docs/TESTING.md).
- Локальний превʼю-сайт: `scripts/seed_local_preview.py` + `SITE_CONFIG=... npm run dev` у `site_template/`.

Повна інструкція перевірки backend, UI, preview-сайту та Vercel:
`docs/TESTING.md`. Однією командою: `./scripts/test_all.sh`.

## Автоматизація (vertical slice)

```
Run connector
  → source records (Porto wellness catalog)
  → dedupe / normalize
  → enrichment + evidence (confirmed|inferred|missing|…)
  → FitQL breakdown + reasons
  → top 5 shortlisted
Human: Approve preview
  → ContentPack (structured JSON, not React rewrite)
  → niche template + theme tokens → SiteConfig
  → deploy + QA gates → preview_ready
  → personalized outreach draft (from research)
Human: Approve outreach → mark sent
```

Preview generation is **template-driven**: AI (optional; deterministic fallback without `OPENAI_API_KEY`) only fills a `ContentPack` (`hero`, `services`, `cta`, `contact`) — never regenerates the Next.js app.
## Структура

```
app/
  core/
    state_machine.py   # життєвий цикл ліда (§3.2) + guard переходів
    schemas.py         # контракти: SourceRecord, InboundLead, SiteConfig (§2.2, §5.2)
  models.py            # канонічні сутності (§3.3): Business, Evidence, Qualification,
                       # WebsiteBuild, Proposal, Consent, Suppression, Message, Event...
  adapters/
    csv_import.py      # P0-конектор: CSV/manual import, checkpoint/resume (§3.4)
    hosting.py         # hosting-адаптер: Vercel + локальний stub (§5.2)
  services/
    ingestion.py       # normalize → dedupe → provenance; спільний шлях для всіх джерел
    dedupe.py          # нормалізація назв/телефонів, identity resolution
    scoring.py         # FitQL 30/25/20/15/10, пороги 75/55, Tier A/B (§4.3)
    site_factory.py    # evidence → SiteConfig → QA → deploy → expiry (§5)
    proposal.py        # пропозиція з версіонованого Pricebook (Kaapora-based, §5.4, §6)
    outreach.py        # guards: approval, consent, suppression, 4-touch max (§7, §8.2)
    events.py          # immutable event log + state transitions
    config_loader.py   # версіоновані конфіги, publish/rollback (§2.3)
    analytics.py       # KPI (§8.1)
  main.py              # FastAPI control plane
config/                # версіонована конфігурація (не код):
  markets/  scoring/  pricebook/  messaging/  templates/
  # кожен домен: v1.yaml (immutable) + ACTIVE (publish/rollback)
tests/                 # 32 тести: dedupe, скоринг, state machine, suppression,
                       # source failure/resume, low-confidence facts, 1000-record load
site_template/         # Next.js шаблон превʼю-сайту (wellness-next-template)
  app/                 # сторінка, booking-форма (шле на бекенд /preview-form), стилі
  lib/site-config.js   # читання SiteConfig з env SITE_CONFIG
  sample-config.json   # синтетичний приклад для локальної розробки
docs/
  BOT_AI_AGENT_SUMMARY.md    # summary попереднього проекту bot_ai-agent
  ARCHITECTURE_SOURCE.txt    # текст вихідного docx архітектури
```

## Ключові інваріанти (з архітектури)

- **Жодного відправлення без approval + consent + suppression-check**; WhatsApp лише за opt-in; максимум 4 торкання; reply/opt-out/bounce зупиняє послідовність.
- **Кожен перехід стану** пише immutable Event з actor, reason, версією конфігурації, correlation ID та idempotency key.
- **Факти ніколи не вигадуються**: SiteConfig збирається тільки зі збережених Evidence з source/confidence; низька впевненість блокує білд і йде на review.
- **Ціни лише в Pricebook** (версіонований YAML), не в коді і не в промптах.
- **Ідемпотентність**: повторний імпорт/вебхук/санд не створює дублікатів; збійний SourceRun відновлюється з checkpoint.
- Превʼю-сайти noindex, з терміном дії та teardown.

## API (основне)

| Ендпоїнт | Призначення |
|---|---|
| `POST /connectors/{id}/run`, `POST /pipeline/porto-wellness` | vertical slice: discover→enrich→FitQL→shortlist |
| `POST /businesses/{id}/approve-preview` | human gate → template preview + outreach draft |
| `POST /businesses/{id}/outreach-draft` | personalized draft from research snapshot |
| `POST /connectors`, `POST /connectors/{id}/import-csv` | джерела та CSV-імпорт |
| `POST /source-runs/{id}/resume` | відновлення збійного run з checkpoint |
| `POST /webhooks/inbound` | generic inbound контракт (P0; Meta Lead Ads мапиться сюди в P1) |
| `POST /businesses/{id}/evidence`, `/audit`, `/score` | верифікація і FitQL |
| `POST /qualifications/assign-tiers` | Tier A (топ-15) / Tier B (наступні 15) |
| `POST /businesses/{id}/build-preview`, `/proposal` | фабрика сайтів і пропозицій |
| `POST /businesses/{id}/messages`, `/messages/{id}/approve`, `/mark-sent` | guarded outreach |
| `POST /businesses/{id}/suppress` | opt-out/suppression (скасовує чергу) |
| `GET/POST /config/{domain}` | перегляд/publish/rollback версій конфігурації |
| `GET /analytics/kpi` | KPI-зріз (§8.1) |

Авторизація: заголовок `x-admin-token` (P0); вебхук — `x-webhook-secret`.

## Що свідомо поза P0 (згідно з non-goals)

Production Meta Ads, масовий автономний аутріч, холодний WhatsApp/SMS, AI-дзвінки, мультилокалізація, предиктивний ML, booking/payment бекенд. Реальний Next.js-шаблон і Playwright/Lighthouse QA підключаються через уже визначені контракти (`config/templates`, `hosting.py`, `run_qa`).
