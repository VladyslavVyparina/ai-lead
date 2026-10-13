# Summary попереднього проекту: bot_ai-agent

Розташування: `/Users/macbookpro/Desktop/bot_ai-agent` (~2 400 рядків Python)

## Що це

CRM з AI-агентом для обробки лідів через Telegram + AI-дзвінки. Стек: **FastAPI + SQLAlchemy (SQLite) + aiogram 3 + OpenAI + ElevenLabs/Zadarma**.

## Компоненти

| Компонент | Файли | Опис |
|---|---|---|
| Telegram-бот | `app/bot.py` (452 р.) | Діалог з лідом через GPT (gpt-4o-mini), транскрипція голосових, збір телефону, "typing" імітація, санітизація відповідей |
| API/вебхуки | `app/main.py` (352 р.) | FastAPI: вебхуки Zadarma та ElevenLabs post-call (транскрипт, summary, фідбек по дзвінку через GPT, нотифікація ліда в Telegram) |
| Адмін-панель | `app/admin.py` (733 р.) + `templates/` | Jinja2 UI: логін (bcrypt + сесії), список/деталі лідів, AI-knowledge, діагностика телефонії, користувачі/ролі (owner/manager) |
| Телефонія | `app/services/telephony_service.py`, `zadarma_service.py`, `elevenlabs_convai_service.py` | Ініціація AI-дзвінка ліду: Zadarma callback API або ElevenLabs ConvAI через SIP; auto-вибір провайдера |
| Self-learning | `app/services/learning_service.py` | `LearningProfile` — накопичуваний контекст ніші, який доклеюється в system prompt; щоденний фоновий воркер оновлення + `LearningReport` |
| SIP-проксі | `sip_proxy/` | Готові конфіги Asterisk (pjsip, extensions, rtp) + install/configure скрипти для мосту Zadarma ↔ ElevenLabs |
| Лендінг | `site_client/` | Статичний сайт-візитка з формою (SendPulse), email-шаблон, thank-you сторінка |

## Модель даних

`Lead` (telegram_user_id, name, phone, needs, status) → `ChatMessage` (роль/джерело: text/voice/call_feedback) → `CallRecord` (provider, call_id, transcript, summary JSON). Окремо: `LearningProfile`, `LearningReport`, `AdminUser`/`AdminSession`/`AdminProfile`.

## Що варто перевикористати в новому проекті

- Патерн FastAPI + SQLAlchemy + Pydantic Settings з `.env`.
- Механіка вебхуків з ідемпотентним мапінгом записів (пошук за conversation_id / lead_id).
- Підхід "адмінка на Jinja2" для швидкого operator UI.
- Досвід інтеграції телефонії — у новій архітектурі AI-дзвінки поза P0, але адаптерний підхід переноситься.

## Ключові відмінності нового проекту (AI Lead Pipeline)

- Не один бот, а **детермінований оркестратор** зі змінними source-адаптерами і bounded-агентами.
- Уся комерція/скоринг/меседжинг — **версіонована конфігурація**, не код і не промпти.
- Повний state machine ліда (new → … → won/lost/suppressed) з **immutable event log**.
- Outreach лише через **людське затвердження + consent/suppression guards**.
- Фабрика превʼю-сайтів (SiteConfig → Next.js шаблон → deploy) замість чат-діалогу.
