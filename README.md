# SEO Lead Parser

> **EN:** Lead collection and AI qualification pipeline for a digital agency.
> Parses freelance and job boards (FL.ru, HH.ru via OAuth API, Kwork, Workspace),
> scores every lead 1–10 with an LLM against the agency's service profile, pushes
> hot ones to Telegram and shows the rest in a web dashboard.
> Python · async SQLAlchemy · Flask · Playwright · APScheduler.

Система сбора и квалификации лидов для digital-агентства. Парсит заявки
с фриланс- и job-площадок, оценивает каждую через LLM, отправляет горячие
менеджерам в Telegram и показывает остальные в веб-дашборде.

## Зачем

Менеджер вручную обходил четыре площадки несколько раз в день и всё равно
пропускал заявки — они висят по 20 минут, потом их разбирают. Здесь обход
идёт по расписанию, а решение «стоит ли открывать» принимает модель:
до менеджера доходят только те заявки, где агентство реально может выиграть.

## Что делает

- **Парсит 4 источника:** FL.ru, HH.ru (официальный OAuth API), Kwork, Workspace
- **Квалифицирует через LLM:** каждому лиду ставится оценка 1–10 с обоснованием —
  учитывается профиль услуг агентства, бюджет, адекватность ТЗ и признаки
  «холостых» заявок
- **Уведомляет:** лиды выше порога уходят в Telegram сразу, остальные копятся в базе
- **Показывает:** веб-дашборд для просмотра, фильтрации и разбора истории
- **Работает сам:** APScheduler гоняет обход по расписанию, дедупликация не даёт
  прислать одну заявку дважды
- **Не падает от квот:** поддержка нескольких API-ключей с ротацией при исчерпании лимита

## Архитектура

```
APScheduler
    │
    ├── parsers/  ──►  FL.ru · HH.ru (OAuth) · Kwork · Workspace
    │                   (Playwright там, где нет API)
    ▼
дедупликация ──► ai_filter.py  ──►  оценка 1–10 + обоснование
                                          │
                        ┌─────────────────┴─────────────────┐
                        ▼                                   ▼
              notifier.py → Telegram              database.py → SQLite
              (score ≥ порога)                    (async SQLAlchemy)
                                                            │
                                                            ▼
                                                   dashboard/ (Flask)
```

## Стек

Python 3.11 · SQLAlchemy (async) · aiosqlite · Flask · Playwright · APScheduler ·
Google Gemini API · Telegram Bot API

## Запуск

```bash
python -m venv venv && venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
copy .env.example .env                                        # ключи
copy prompts\lead_scoring.example.txt prompts\lead_scoring.txt  # профиль компании
python main.py                  # парсер + планировщик
python dashboard/app.py         # дашборд отдельным процессом
```

**Профиль компании обязателен.** В `prompts/lead_scoring.txt` описываются услуги,
стоп-критерии и шкала оценки — по ним модель и решает, что показывать менеджеру.
Файл вынесен из кода и не коммитится: у каждой компании он свой, а меняется он
чаще самого парсера. Путь переопределяется переменной `SCORING_PROMPT_PATH`.

Для HH.ru нужно завести OAuth-приложение и один раз получить токен:

```bash
python get_hh_token.py
```

## Конфигурация

Всё через `.env`, см. `.env.example`. Основное:

| Переменная | Что делает |
|---|---|
| `GEMINI_API_KEY` / `GEMINI_API_KEYS` | ключ или список ключей для ротации |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | куда слать горячие лиды |
| `HH_CLIENT_ID`, `HH_CLIENT_SECRET`, `HH_ACCESS_TOKEN` | доступ к HH.ru API |
| `MIN_SCORE_TO_NOTIFY` | порог оценки для уведомления |
| `PARSE_INTERVAL_MINUTES` | как часто обходить площадки |

## Структура

```
main.py           точка входа: планировщик + оркестрация обхода
config.py         конфигурация из окружения
parsers/          по модулю на площадку, общий интерфейс
ai_filter.py      LLM-скоринг лида, ротация ключей, ретраи
database.py       модели и доступ к базе (async SQLAlchemy)
notifier.py       отправка в Telegram
scheduler.py      расписание обхода
dashboard/        Flask-интерфейс для менеджеров
tests/            тесты
```

## Ограничения

Часть площадок не отдаёт публичного API, поэтому обход идёт через Playwright
и зависит от вёрстки — селекторы вынесены в модули парсеров и правятся точечно.
Сбор ведётся только по публично опубликованным заявкам.

## Лицензия

MIT
