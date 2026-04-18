# Accumulation Pattern Agent + Token Analyzer

Два инструмента в одной папке:

1. **Accumulation Agent** (`main.py`) — фоновый polling-бот. Следит за токенами из `tokens.json`, шлёт сигнал в Telegram когда складывается паттерн Wyckoff Phase 2 (post-ATH re-accumulation).
2. **Token Analyzer** (`analyze.py`) — one-shot CLI. Кидаешь контракт, получаешь полный отчёт: нарратив, умные деньги, риски, вердикт BUY/WATCH/RISKY/AVOID.

Plus:
- **`backtest.py`** — прогон детектора на исторических данных с метриками precision / avg ROI.
- **`wayan_handler.py`** — готовый aiogram 3 router для интеграции в `@Wayan_pirate_bot`.

## Быстрый старт

```bash
python3 -m venv venv && source venv/bin/activate
pip install httpx python-dotenv
cp .env.example .env
# заполни .env — минимум TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID
```

### Анализ одного токена

```bash
python analyze.py <contract_address>
python analyze.py <contract> --chain base --send
python analyze.py <contract> --json
```

### Мониторинг watchlist

```bash
python main.py --add <ADDRESS> solana SYMBOL    # добавить токен
python main.py                                   # balanced
python main.py --mode aggressive                 # больше сигналов
python main.py --mode conservative               # точнее, реже
python main.py --once --verbose                  # один прогон
```

### Backtest

```bash
# Один токен за последний год
python backtest.py --address <ADDR> --chain solana --forward-days 30

# Пачкой из JSON-списка
python backtest.py --tokens my_tokens.json --mode balanced
```

## Архитектура

| Модуль              | Роль                                                     |
|---------------------|----------------------------------------------------------|
| `main.py`           | polling loop агента, CLI                                 |
| `analyze.py`        | CLI для one-shot анализа                                 |
| `detector.py`       | паттерн аккумуляции, scoring 0–100, tier                |
| `token_analyzer.py` | оркестратор one-shot анализа                            |
| `narrative.py`      | классификация в 20 нарративов                           |
| `smart_money.py`    | анализ публичных trades (buy/sell, крупные покупки)     |
| `risk_analyzer.py`  | риски, concentration, соц.сети                          |
| `sm_db.py`          | интеграция с WAYNE_PIRATE smart-money DB                |
| `rugcheck.py`       | Rugcheck.xyz API (Solana — LP lock, authorities, рисками)|
| `coingecko.py`      | lookup по контракту (ATH, description, categories)       |
| `helius.py`         | реальные wallet addresses через Helius (Solana)          |
| `data_fetcher.py`   | DexScreener + GeckoTerminal + Birdeye holders fallback   |
| `state.py`          | atomic JSON persistence, ATH tracking, tier-aware cooldown|
| `telegram.py`       | форматирование и отправка алертов                        |
| `backtest.py`       | offline прогон детектора на истории                      |

## Что нового vs первая версия (важно)

**Критические исправления:**
- ✅ **Hard-фильтры блокируют сигнал**: раньше score решал всё, теперь mcap/liquidity/age/holders out-of-range → NOISE, и алерт не отправляется
- ✅ **Реальный ATH из CoinGecko** в агенте (раньше использовался текущий mcap как placeholder — drawdown всегда был 0)
- ✅ **Tier-aware cooldown**: WATCHLIST больше не блокирует апгрейд до SIGNAL/STRONG
- ✅ **Atomic state.json write** (tmp + os.replace) — крах не оставит битый JSON
- ✅ **Корректный no_new_low_days** — счёт от позиции реального минимума
- ✅ **Real Spring recovery_hours** по hourly-свечам (был hardcoded 12.0)
- ✅ **`--chain auto`** — работает для agent и analyzer

**Новые возможности:**
- ✅ **Rugcheck** для Solana: LP lock %, mint/freeze authority, top holders concentration
- ✅ **Helius path** для Solana: реальные wallet addresses вместо tx_hash[:10] прокси
- ✅ **SM DB integration**: бонус +до 30 к score когда curated wallets из WAYNE_PIRATE докупают токен
- ✅ **Birdeye holders fallback** когда GeckoTerminal возвращает 0
- ✅ **CoinGecko lookup by contract** — точный match, без путаницы по тикерам
- ✅ **Backtest harness** с метриками precision (2x) / avg ROI

## Настройка .env

| Переменная              | Обязательно | Назначение                                   |
|-------------------------|-------------|----------------------------------------------|
| `TELEGRAM_BOT_TOKEN`    | ✅          | бот для алертов                              |
| `TELEGRAM_CHAT_ID`      | ✅          | куда слать алерты                            |
| `BIRDEYE_API_KEY`       | optional    | fallback holders count для Solana            |
| `HELIUS_API_KEY`        | optional    | real smart-money wallet tracking (Solana)    |
| `WAYAN_SM_DB_PATH`      | optional    | путь к `bot.db` WAYNE_PIRATE для SM-бонуса   |

Без optional-ключей всё работает, но с деградацией качества: без Helius → агрегаты buy/sell без уникальных кошельков; без SM DB → никакого SM-бонуса; без Birdeye → holders=0 для большинства Solana-токенов.

## Интеграция с @Wayan_pirate_bot

`wayan_handler.py` — готовый aiogram 3 router. Чтобы добавить `/analyze` команду в существующего бота:

1. Скопируй всю папку `agent/` в `WAYNE_PIRATE/solana-smart-money-bot/bot/analyze_agent/`
2. В `bot/telegram_bot.py` добавь:
   ```python
   from bot.analyze_agent.wayan_handler import analyze_router
   dp.include_router(analyze_router)
   ```
3. Убедись что `httpx` и `python-dotenv` уже в requirements (должны быть).

Schema для watchlist (опциональная часть handler'а) — нужно будет добавить таблицу `user_watchlist` в `db/repository.py` перед раскомментированием `/watch`.

## Параметры режимов

| Параметр              | Aggressive | Balanced | Conservative |
|-----------------------|------------|----------|--------------|
| Min mcap              | $300k      | $500k    | $1M          |
| Max mcap              | $20M       | $15M     | $10M         |
| Min drawdown          | 45%        | 50%      | 60%          |
| Min liquidity         | $50k       | $80k     | $120k        |
| Min consolidation     | 10d        | 14d      | 21d          |
| Volume spike          | x2         | x3       | x4           |
| Signal threshold      | 55         | 65       | 72           |
| Cooldown (SIGNAL+)    | 4h         | 4h       | 4h           |
| Cooldown (WATCHLIST)  | 1h         | 1h       | 1h           |

## Тиры сигналов

| Тир         | Скор   | Что делать                       |
|-------------|--------|----------------------------------|
| 🔥 STRONG   | 80–100 | Все фильтры + сильный сигнал      |
| 🟢 SIGNAL   | 65–79  | Хороший сигнал, можно входить     |
| 👀 WATCHLIST| 45–64  | Слежение, не входить              |
| ⚫ NOISE    | 0–44   | Не отправляется                   |

## Что детектирует агент

| Компонент          | Описание                                         |
|--------------------|--------------------------------------------------|
| Drawdown от ATH    | Токен упал 50–97% от исторического максимума    |
| Консолидация       | Боковик 14–45 дней, диапазон ≤ 30%              |
| Объём высыхает     | Объём в боковике < 30% объёма при падении       |
| Нет нового лоя     | 10+ дней без обновления минимума                |
| Spring             | Пробой вниз 5–15% с возвратом (реальный hourly) |
| Объёмный спайк     | Текущий объём в 3x+ выше среднего               |
| **SM activity**    | Curated wallets из WAYNE_PIRATE закупаются      |
| **Rugcheck**       | mint/freeze renounced, LP locked, top-holders    |

## Стратегия выхода (в алерте)

- **ТП1 (x2)** → продать 20%
- **ТП2 (x4)** → продать 25%
- **ТП3 (ATH)** → продать 25%
- **ТП4 (x2 ATH)** → продать 20%
- **Остаток 10%** — лотерейный билет

**Экстренный выход:** пробой ниже зоны Spring → выход 100%.
