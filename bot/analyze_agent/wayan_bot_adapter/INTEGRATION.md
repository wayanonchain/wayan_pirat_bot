# Интеграция модуля аккумуляции в @Wayan_pirate_bot

Две независимые механики:

1. **Discovery** — раз в 6 часов: находит в `token_buys` токены, где ≥2 SM-кошелька покупали за последние 72h → добавляет в `accumulation_watchlist`.
2. **Monitor** — раз в 15 минут: прогоняет Wyckoff-детектор по watchlist → пишет сигналы → шлёт алерт в лог-чат, если складывается паттерн.

Архитектура нулевого риска: вся функциональность в отдельном подпакете `bot/analyze_agent/`, прод-код бота меняется на **3 строчки**.

---

## Шаг 1. Скопировать папку

На MacBook (исходник):
```
/Users/mac/Desktop/agent/
    config.py, detector.py, data_fetcher.py, state.py, ...       ← standalone agent
    wayan_bot_adapter/                                            ← adapter layer
        __init__.py, migrate.py, repository.py, discovery.py,
        monitor.py, handlers.py, scheduler_jobs.py, alerter.py,
        migrations.sql
```

Копируем целиком как подпакет бота:
```bash
cp -R /Users/mac/Desktop/agent \
      /Users/mac/Documents/WAYNE/WAYNE_PIRATE/solana-smart-money-bot/bot/analyze_agent
```

Структура после копирования:
```
solana-smart-money-bot/
└── bot/
    ├── telegram_bot.py
    ├── course.py
    └── analyze_agent/            ← всё, что было в Desktop/agent/
        ├── config.py
        ├── detector.py
        ├── ...
        └── wayan_bot_adapter/
            └── ...
```

## Шаг 2. Установить зависимости

Новых runtime-зависимостей кроме `httpx` и `aiosqlite` (уже в боте) нет. На всякий случай:
```bash
cd /Users/mac/Documents/WAYNE/WAYNE_PIRATE/solana-smart-money-bot
source venv/bin/activate   # или pip прямо в venv
pip install -r bot/analyze_agent/requirements.txt
```

## Шаг 3. Применить миграцию к bot.db

**Локально (тест):**
```bash
cd /Users/mac/Documents/WAYNE/WAYNE_PIRATE/solana-smart-money-bot
python bot/analyze_agent/wayan_bot_adapter/migrate.py data/bot.db
```

Ожидаемый вывод:
```
✅ Migration applied. Present tables: ['accumulation_signals', 'accumulation_state', 'accumulation_watchlist']
```

**На VPS:**
```bash
ssh -i ~/.ssh/id_wayne_server root@136.244.91.3
cd /opt/wayan_pirat_bot
source venv/bin/activate
python bot/analyze_agent/wayan_bot_adapter/migrate.py data/bot.db
```

Миграция идемпотентная — можно применять повторно без последствий.

## Шаг 4. Зарегистрировать router (1 строка)

В `bot/telegram_bot.py` рядом с существующим `dp.include_router(course_router)`:

```python
from bot.analyze_agent.wayan_bot_adapter.handlers import acc_router
dp.include_router(acc_router)
```

Доступные команды:

| Команда                  | Кто           | Описание                                     |
|--------------------------|---------------|----------------------------------------------|
| `/analyze <contract>`    | **все**       | one-shot анализ токена                       |
| `/acc`                   | admin         | посмотреть текущий watchlist                 |
| `/acc_add <contract>`    | admin         | вручную добавить токен                       |
| `/acc_remove <contract>` | admin         | убрать токен                                 |
| `/acc_discover`          | admin         | принудительный SM-discovery прямо сейчас     |
| `/acc_scan`              | admin         | принудительный monitor pass прямо сейчас     |

## Шаг 5. Зарегистрировать scheduled jobs (1 строка)

В `core/scheduler.py` в функции `start_scheduler()` — после существующих `scheduler.add_job(...)` и перед `scheduler.start()`:

```python
from bot.analyze_agent.wayan_bot_adapter.scheduler_jobs import register_accumulation_jobs
register_accumulation_jobs(scheduler)
```

Эта функция добавит два job'а:

- `accumulation_discover` — каждые 6 часов (17-я минута)
- `accumulation_monitor` — каждые 15 минут

## Шаг 6. Настроить окружение

Единственное обязательное требование — модуль должен уметь найти `bot.db`. Он это умеет автоматически через `config.settings.DB_PATH` (когда бот импортируется как пакет). Ничего добавлять не надо.

Опционально в `.env` бота добавить:

```bash
# Если хочешь использовать Helius (реальные wallet addresses в smart-money анализе)
# — ключ уже есть в .env для webhook-флоу, переиспользуется автоматически.

# Если хочешь Birdeye holders fallback:
# — ключ тоже уже есть, переиспользуется.
```

## Шаг 7. Деплой

```bash
# Локально: закоммить изменения
cd /Users/mac/Documents/WAYNE/WAYNE_PIRATE/solana-smart-money-bot
git add bot/analyze_agent bot/telegram_bot.py core/scheduler.py
git commit -m "feat: add accumulation discovery + monitor pipeline"
git push origin feature/meteora-course

# VPS
ssh -i ~/.ssh/id_wayne_server root@136.244.91.3 "
  cd /opt/wayan_pirat_bot &&
  git fetch && git reset --hard origin/feature/meteora-course &&
  source venv/bin/activate &&
  python bot/analyze_agent/wayan_bot_adapter/migrate.py data/bot.db &&
  systemctl restart wayan-bot &&
  sleep 2 && systemctl status wayan-bot --no-pager
"
```

## Валидация после деплоя

1. Отправь боту `/analyze EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v` — должен прийти полный отчёт по USDC за 10–20 секунд.
2. От админа: `/acc_discover` — должен вернуть количество кандидатов.
3. `/acc` — увидишь добавленные токены.
4. Через 15 минут в логах:
   ```bash
   journalctl -u wayan-bot -f | grep accumulation
   ```
   должно быть `[monitor] processed=N fired=M`.
5. SQL-проверка прямо на VPS:
   ```bash
   sqlite3 /opt/wayan_pirat_bot/data/bot.db \
     "SELECT symbol, last_tier, last_score FROM accumulation_watchlist ORDER BY added_at DESC LIMIT 10;"
   ```

## Откат (если что-то пошло не так)

Ничего из этого не трогает существующие таблицы — удаление трёх новых таблиц и двух строк из `bot/telegram_bot.py` и `core/scheduler.py` полностью откатывает всё:

```sql
DROP TABLE IF EXISTS accumulation_signals;
DROP TABLE IF EXISTS accumulation_state;
DROP TABLE IF EXISTS accumulation_watchlist;
```

## Тюнинг параметров

Все пороги — в коде adapter'а, меняются на лету через переменные окружения или прямую правку:

| Где                                         | Параметр           | По умолчанию |
|---------------------------------------------|--------------------|--------------|
| `scheduler_jobs.py` (_job_discover call)    | window_hours       | 72           |
| "                                           | min_unique_wallets | 2            |
| "                                           | min_total_usd      | 500          |
| `scheduler_jobs.py` CronTrigger             | discover cadence   | 6 часов      |
| "                                           | monitor cadence    | 15 мин       |
| `repository.py` (mark_stale_old_entries)    | max_age_days       | 60           |
| `agent/config.py` (BALANCED)                | cooldown_hours     | 4            |

Если в первый месяц будешь видеть слишком много шума — подними `min_unique_wallets` до 3 или `min_total_usd` до 2000.
