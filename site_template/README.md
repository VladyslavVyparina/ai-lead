# wellness-next-template

Категорійний Next.js шаблон превʼю-сайту (massage/wellness + beauty), який рендериться з валідованого `SiteConfig` — контракт у бекенді (`app/core/schemas.py → SiteConfig`). Зареєстрований у Template Registry: `config/templates/v1.yaml` (id `wellness-next-template`).

## Принципи (з архітектури §5)

- Рутинний білд для ліда змінює **лише SiteConfig** — код шаблону не редагується під конкретний лід.
- Сторінка `force-dynamic`: `SITE_CONFIG` читається **на запит**, не під час `npm run build` (інакше всі ліди бачили б один демо-контент).
- У `next.config` **немає** `env.SITE_CONFIG` — він би «запік» значення на етапі збірки.
- Факт відсутній → секція не рендериться. Нічого не вигадується (ціни без верифікації не показуються).
- Preview-режим: банер «conceito», `noindex`, форма шле запити на контрольований endpoint бекенда `POST /preview-form` (не бізнесу).
- Кастомізація на лід: назва, опис, послуги/ціни, контакти (click-to-call / WhatsApp / mailto), адреса/години, колір бренду, SEO.

## Запуск локально

```bash
cd site_template
npm install
npm run dev   # http://localhost:3000 з sample-config.json (форма НЕ працюватиме — фейковий token)
```

Для робочої форми потрібен seed з бекенда (див. `docs/TESTING.md`):

```bash
# з кореня репо, при запущеному uvicorn на :8000
.venv/bin/python scripts/seed_local_preview.py
cd site_template
SITE_CONFIG="$(cat .local-site-config.json)" npm run dev -- --port 3100
```

З реальним конфігом у production-режимі:

```bash
SITE_CONFIG="$(cat my-lead-config.json)" npm run build   # можна і без SITE_CONFIG — збірка шаблону
SITE_CONFIG="$(cat my-lead-config.json)" npm start         # конфіг обовʼязковий на runtime
```

## Деплой

Hosting-адаптер бекенда (`app/adapters/hosting.py`, Vercel) передає `SITE_CONFIG` як env при деплої. Щоб увімкнути реальний деплой: опублікуйте цю папку як репозиторій/проект у Vercel і додайте `VERCEL_TOKEN` у `.env` бекенда.
