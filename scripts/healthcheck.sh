#!/usr/bin/env bash
# External liveness watchdog for wayan-bot.
#
# Probes the FastAPI /health endpoint with a 5s timeout. The endpoint runs on
# the same event loop that handles the Helius webhook, so if the loop is
# frozen the probe times out — that is the failure mode this watchdog exists
# to catch (see incident 2026-05-04 where retry-storm froze the loop while
# the process stayed "active").
#
# On 1st consecutive failure: capture py-spy stack dump (silent — no TG).
# On 2nd consecutive failure: capture second dump + restart service (silent).
# On restart-command failure: alert TG (this is the only path that pages now).
# On success: reset the counter (silent).
#
# Telegram noise was deliberately removed 2026-05-05 — transient freezes
# self-heal via restart, and the per-failure alerts were flooding the chat.
# Stack dumps still land in $DUMP_DIR for post-mortem.
#
# Stack dumps are written to $DUMP_DIR/freeze-<timestamp>.txt — they tell us
# where the loop was stuck when it stopped responding (added 2026-05-04 after
# repeated freezes with no smoking-gun in journalctl).
#
# Designed to be run by a systemd timer every few minutes.

set -u

ENV_FILE="${ENV_FILE:-/opt/wayan_pirat_bot/.env}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8080/health}"
SERVICE="${SERVICE:-wayan-bot}"
STATE_FILE="${STATE_FILE:-/var/lib/wayan-bot/health_failures}"
TIMEOUT="${TIMEOUT:-5}"
DUMP_DIR="${DUMP_DIR:-/var/log/wayan-bot}"
PY_SPY="${PY_SPY:-/usr/local/bin/py-spy}"
DUMP_RETAIN="${DUMP_RETAIN:-20}"

mkdir -p "$(dirname "$STATE_FILE")"
mkdir -p "$DUMP_DIR"

# Load .env into the environment (only the keys we use, no expansion games).
# shellcheck disable=SC1090
TELEGRAM_BOT_TOKEN="$(grep -E '^TELEGRAM_BOT_TOKEN=' "$ENV_FILE" | head -1 | cut -d= -f2-)"
LOG_CHAT_ID="$(grep -E '^LOG_CHAT_ID=' "$ENV_FILE" | head -1 | cut -d= -f2- || true)"
LOG_CHAT_ID="${LOG_CHAT_ID:--1003833809842}"
LOG_CHAT_ERRORS_THREAD_ID="$(grep -E '^LOG_CHAT_ERRORS_THREAD_ID=' "$ENV_FILE" | head -1 | cut -d= -f2- || true)"
LOG_CHAT_ERRORS_THREAD_ID="${LOG_CHAT_ERRORS_THREAD_ID:-60}"

read_failures() {
    if [[ -f "$STATE_FILE" ]]; then
        cat "$STATE_FILE" 2>/dev/null || echo 0
    else
        echo 0
    fi
}

write_failures() {
    echo "$1" > "$STATE_FILE"
}

tg_alert() {
    local text="$1"
    if [[ -z "$TELEGRAM_BOT_TOKEN" ]]; then
        echo "no TELEGRAM_BOT_TOKEN, cannot alert" >&2
        return
    fi
    curl -fsS -m 10 -X POST \
        "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        -d "chat_id=${LOG_CHAT_ID}" \
        -d "message_thread_id=${LOG_CHAT_ERRORS_THREAD_ID}" \
        -d "parse_mode=HTML" \
        --data-urlencode "text=${text}" \
        > /dev/null || echo "tg alert failed" >&2
}

probe() {
    local body http_code
    body="$(curl -fsS -m "$TIMEOUT" -w '\n%{http_code}' "$HEALTH_URL" 2>&1)" || return 1
    http_code="$(printf '%s' "$body" | tail -n1)"
    [[ "$http_code" == "200" ]]
}

# Capture a py-spy stack dump of the bot's main process. We do this *before*
# the restart so we can see where the event loop was stuck. Output goes to
# $DUMP_DIR/freeze-<UTC-timestamp>-<tag>.txt; only the most recent
# $DUMP_RETAIN dumps are kept.
capture_dump() {
    local tag="$1"
    local pid ts dump_path
    pid="$(systemctl show -p MainPID --value "$SERVICE" 2>/dev/null || echo 0)"
    if [[ -z "$pid" || "$pid" == "0" ]]; then
        echo "no MainPID for $SERVICE, skipping py-spy dump" >&2
        return 1
    fi
    if [[ ! -x "$PY_SPY" ]]; then
        echo "py-spy not at $PY_SPY, skipping dump" >&2
        return 1
    fi
    ts="$(date -u +%Y%m%dT%H%M%SZ)"
    dump_path="${DUMP_DIR}/freeze-${ts}-${tag}.txt"
    {
        echo "# py-spy dump captured by healthcheck.sh"
        echo "# host:    $(hostname -s)"
        echo "# pid:     ${pid}"
        echo "# tag:     ${tag}"
        echo "# time:    ${ts}"
        echo "# service: ${SERVICE}"
        echo "----- py-spy dump -----"
        timeout 15 "$PY_SPY" dump --pid "$pid" 2>&1
    } > "$dump_path" 2>&1
    # Trim old dumps (keep newest $DUMP_RETAIN).
    ls -1t "$DUMP_DIR"/freeze-*.txt 2>/dev/null \
        | tail -n +$((DUMP_RETAIN + 1)) \
        | xargs -r rm -f
    DUMP_PATH="$dump_path"
    return 0
}

failures="$(read_failures)"
hostname_short="$(hostname -s)"

if probe; then
    write_failures 0
    exit 0
fi

failures=$((failures + 1))
write_failures "$failures"

# Always capture a stack dump (cheap; written to disk only). Useful for
# post-mortem of the freeze, but we no longer page TG on every freeze.
capture_dump "f${failures}" || true

if [[ "$failures" -ge 2 ]]; then
    # The "is the bot dead?" path. Restart usually fixes the freeze; only
    # page TG if the restart command itself fails — that's the unrecoverable
    # case the user actually wants to be woken up for.
    if ! systemctl restart "$SERVICE"; then
        tg_alert "❌ <b>wayan-bot</b> мёртв: <code>systemctl restart</code> упал на <code>${hostname_short}</code>. Бот не работает, нужно руками."
    fi
    write_failures 0
fi
