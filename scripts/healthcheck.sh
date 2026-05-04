#!/usr/bin/env bash
# External liveness watchdog for wayan-bot.
#
# Probes the FastAPI /health endpoint with a 5s timeout. The endpoint runs on
# the same event loop that handles the Helius webhook, so if the loop is
# frozen the probe times out — that is the failure mode this watchdog exists
# to catch (see incident 2026-05-04 where retry-storm froze the loop while
# the process stayed "active").
#
# On 1st consecutive failure: alert to Telegram error thread.
# On 2nd consecutive failure: alert + `systemctl restart wayan-bot`.
# On success: reset the counter.
#
# Designed to be run by a systemd timer every few minutes.

set -u

ENV_FILE="${ENV_FILE:-/opt/wayan_pirat_bot/.env}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8080/health}"
SERVICE="${SERVICE:-wayan-bot}"
STATE_FILE="${STATE_FILE:-/var/lib/wayan-bot/health_failures}"
TIMEOUT="${TIMEOUT:-5}"

mkdir -p "$(dirname "$STATE_FILE")"

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

failures="$(read_failures)"
hostname_short="$(hostname -s)"

if probe; then
    if [[ "$failures" -gt 0 ]]; then
        tg_alert "✅ <b>wayan-bot</b> healthcheck recovered on <code>${hostname_short}</code> after ${failures} consecutive failures."
    fi
    write_failures 0
    exit 0
fi

failures=$((failures + 1))
write_failures "$failures"

if [[ "$failures" -ge 2 ]]; then
    tg_alert "🚨 <b>wayan-bot</b> healthcheck failed ${failures}× in a row on <code>${hostname_short}</code> — restarting service. URL: <code>${HEALTH_URL}</code>"
    systemctl restart "$SERVICE" || tg_alert "❌ <b>wayan-bot</b> restart command failed on <code>${hostname_short}</code>."
    write_failures 0
else
    tg_alert "⚠️ <b>wayan-bot</b> healthcheck failed (#${failures}) on <code>${hostname_short}</code>. Will restart on next failure. URL: <code>${HEALTH_URL}</code>"
fi
