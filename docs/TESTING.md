# Як тестувати AI Lead Pipeline

Три шари: **автотести**, **бекенд/UI**, **превʼю-сайт**.

## 0. Підготовка (один раз)

```bash
cd "/root/AI Lead"   # або локальний шлях
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp -n .env.example .env
# у .env мають бути реальні ADMIN_TOKEN і INBOUND_WEBHOOK_SECRET
```

Для сайту потрібен Node (на Mac можна використати локальний `.tools/node`, якщо вже є):

```bash
cd site_template
npm install
```

## 1. Автотести (обовʼязково перед деплоєм)

```bash
.venv/bin/python -m pytest -q
# очікується: 57+ passed
```

Або все разом (бекенд + збірка сайту):

```bash
bash scripts/test_all.sh
```

## 2. Запуск бекенда і операторського UI

```bash
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Відкрийте:

| URL | Що це |
|---|---|
| http://ВАШ_IP:8000/ui | операторський UI (логін = `ADMIN_TOKEN`) |
| http://ВАШ_IP:8000/docs | Swagger API |
| http://ВАШ_IP:8000/health | `{"ok":true}` |

### Мінімальний ручний сценарій в UI

1. **Джерела** → створіть конектор `csv_import` → додайте бізнес вручну з лінком на джерело.
2. **Ліди** → відкрийте картку → **Верифікувати** контакти → збережіть **аудит** (зніміть «Має сайт», якщо сайту немає) → додайте факти (`rating`, `review_count`, …, `address`) → **Оцінити**.
3. Змініть стан на `shortlisted` → **Збудувати превʼю-сайт** → **Створити пропозицію**.
4. Стан `approved` → **Аутріч** → чернетка → Затвердити → позначте відправленим після реальної відправки листа.

## 3. Превʼю-сайт локально (форма → бекенд → дашборд)

Важливо: `sample-config.json` має **фейковий** `form_token`. Для реальної форми спочатку згенеруйте seed:

```bash
# термінал 1 — бекенд (той самий pipeline.db)
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000

# термінал 2 — seed (пише site_template/.local-site-config.json з живим токеном)
.venv/bin/python scripts/seed_local_preview.py

# термінал 3 — сайт з цим конфігом
cd site_template
SITE_CONFIG="$(cat .local-site-config.json)" npm run dev -- --port 3100
```

Відкрийте http://127.0.0.1:3100 — має бути назва «Estúdio Local Preview».
Заповніть форму «Pedido de marcação» → на **тому ж** бекенді `/ui` зʼявиться заявка.

**Часта помилка:** превʼю крутиться локально (`form_endpoint=http://localhost:8000/...`), а дашборд дивляться на VPS (`http://173.x.x.x:8000/ui`). Це **різні БД** — заявка є лише там, куди пішов POST. Дивіться дашборд того ж хоста, що в `form_endpoint`.

Після зміни `.env` / `app/config.py` **перезапустіть uvicorn**, інакше CORS лишиться старим.

Швидка перевірка без браузера:

```bash
.venv/bin/python scripts/smoke_test.py \
  --base-url http://127.0.0.1:8000 \
  --admin-token "$ADMIN_TOKEN" \
  --preview-origin http://127.0.0.1:3100
```

## 4. Що саме перевіряти (чеклист)

### Бекенд / UI
- [ ] `/health` → ok
- [ ] логін з неправильним токеном не пускає
- [ ] CSV-імпорт і quick-add створюють ліди
- [ ] без верифікації контактів превʼю-білд блокується
- [ ] після скорингу стан стає `fitql` / `rejected`
- [ ] suppression блокує затвердження наступного листа

### Превʼю-сайт
- [ ] банер «conceito» і `noindex` у preview-режимі
- [ ] відсутні факти не показуються (немає вигаданих цін)
- [ ] click-to-call / WhatsApp / mailto працюють
- [ ] форма шле на `PUBLIC_BASE_URL/preview-form` і зʼявляється на дашборді
- [ ] різні `SITE_CONFIG` дають різний контент (сторінка `force-dynamic`, не static bake)

### CORS
- форма з `http://localhost:3100`, `http://127.0.0.1:3100` або `https://*.vercel.app` повинна проходити OPTIONS/POST
- якщо в `.env` є старий `PREVIEW_ORIGIN_REGEX` без `127.0.0.1` — закоментуйте рядок (авто-regex з `PUBLIC_BASE_URL`)
- чужий Origin — без `Access-Control-Allow-Origin`

## 5. На сервері після оновлення коду

```bash
cd "/root/AI Lead"
pkill -f "uvicorn app.main:app" || true
# якщо мінялась схема і база тестова:
# rm -f pipeline.db
.venv/bin/python -m pytest -q
nohup .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 > app.log 2>&1 &
curl -s http://localhost:8000/health
```

Реальний деплой шаблону на Vercel (замість stub-URL) — окремий крок: проект з `site_template/` + `VERCEL_TOKEN` у `.env`.
