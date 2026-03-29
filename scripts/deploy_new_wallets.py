#!/usr/bin/env python3
"""
Deploy new wallets to VPS.
==========================
1. Copy wallet_database.json to VPS
2. Import new wallets into SQLite (without losing existing data)
3. Recreate Helius webhooks with all active addresses

Run from MacBook:
    python scripts/deploy_new_wallets.py [--dry-run]
"""

import asyncio
import json
import subprocess
import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

DATA_DIR = Path(__file__).parent.parent / "data"
WALLET_DB_PATH = DATA_DIR / "wallet_database.json"

VPS_HOST = "root@136.244.91.3"
VPS_KEY = Path.home() / ".ssh" / "id_wayne_server"
VPS_BOT_DIR = "/opt/wayan_pirat_bot"
VPS_DATA_DIR = f"{VPS_BOT_DIR}/data"

# Helius free plan: max 100 addresses per webhook
ADDRESSES_PER_WEBHOOK = 100


def ssh_cmd(cmd: str, timeout: int = 30) -> str:
    """Run command on VPS via SSH."""
    full = f'ssh -i {VPS_KEY} {VPS_HOST} "{cmd}"'
    result = subprocess.run(full, shell=True, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        print(f"  SSH error: {result.stderr.strip()}")
    return result.stdout.strip()


def scp_to_vps(local_path: str, remote_path: str):
    """Copy file to VPS."""
    cmd = f"scp -i {VPS_KEY} {local_path} {VPS_HOST}:{remote_path}"
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        print(f"  SCP error: {result.stderr.strip()}")
        return False
    return True


async def recreate_webhooks(addresses: list[str], webhook_url: str, dry_run: bool = False):
    """Delete all existing webhooks and create new ones with all addresses."""
    from api.helius_client import get_webhooks, delete_webhook, create_webhook

    # Get existing webhooks
    existing = await get_webhooks()
    print(f"\n  Existing webhooks: {len(existing)}")
    for wh in existing:
        print(f"    {wh.get('webhookID', '?')[:20]}... ({len(wh.get('accountAddresses', []))} addresses)")

    if dry_run:
        chunks = [addresses[i:i + ADDRESSES_PER_WEBHOOK]
                   for i in range(0, len(addresses), ADDRESSES_PER_WEBHOOK)]
        print(f"\n  [DRY RUN] Would create {len(chunks)} webhooks for {len(addresses)} addresses")
        return

    # Delete old webhooks
    print(f"\n  Deleting {len(existing)} old webhooks...")
    for wh in existing:
        wh_id = wh.get("webhookID")
        ok = await delete_webhook(wh_id)
        status = "✅" if ok else "❌"
        print(f"    {status} Deleted {wh_id[:20]}...")

    # Create new webhooks in chunks of 100
    chunks = [addresses[i:i + ADDRESSES_PER_WEBHOOK]
               for i in range(0, len(addresses), ADDRESSES_PER_WEBHOOK)]

    print(f"\n  Creating {len(chunks)} webhooks for {len(addresses)} addresses...")
    created = 0
    for i, chunk in enumerate(chunks, 1):
        result = await create_webhook(chunk, webhook_url)
        if result:
            created += 1
            print(f"    ✅ Webhook {i}/{len(chunks)}: {len(chunk)} addresses -> {result.get('webhookID', '?')[:20]}...")
        else:
            print(f"    ❌ Webhook {i}/{len(chunks)}: FAILED ({len(chunk)} addresses)")

    print(f"\n  Created {created}/{len(chunks)} webhooks")
    return created


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Show what would happen without doing it")
    parser.add_argument("--skip-webhooks", action="store_true", help="Skip webhook recreation")
    args = parser.parse_args()

    print("=" * 60)
    print("  DEPLOY NEW WALLETS TO VPS")
    print("=" * 60)

    # Load local wallet DB
    if not WALLET_DB_PATH.exists():
        print("❌ wallet_database.json not found locally!")
        sys.exit(1)

    with open(WALLET_DB_PATH) as f:
        db = json.load(f)

    wallets = db.get("wallets", db)
    total = len(wallets)
    active_wallets = {k: v for k, v in wallets.items() if v.get("status") == "ACTIVE"}
    active_count = len(active_wallets)
    print(f"\n  Local DB: {total} total, {active_count} ACTIVE")

    # Check VPS current state
    print(f"\n  Checking VPS...")
    vps_info = ssh_cmd(f"cd {VPS_BOT_DIR} && python3 -c 'import json; db=json.load(open(\"data/wallet_database.json\")); w=db.get(\"wallets\",db); a=sum(1 for v in w.values() if v.get(\"status\")==\"ACTIVE\"); print(f\"VPS: {{len(w)}} total, {{a}} ACTIVE\")'")
    print(f"  {vps_info}")

    new_count = active_count - 425  # approximate new wallets
    print(f"\n  New wallets to add: ~{new_count}")

    if args.dry_run:
        webhooks_needed = (active_count + ADDRESSES_PER_WEBHOOK - 1) // ADDRESSES_PER_WEBHOOK
        print(f"\n  [DRY RUN] Would need {webhooks_needed} Helius webhooks")
        print(f"  [DRY RUN] Would copy wallet_database.json to VPS")
        print(f"  [DRY RUN] Would import {active_count} wallets into SQLite")
        return

    # Step 1: Backup VPS DB
    print(f"\n[1] Backing up VPS database...")
    ssh_cmd(f"cp {VPS_DATA_DIR}/wallet_database.json {VPS_DATA_DIR}/wallet_database_backup_pre_3000.json")
    ssh_cmd(f"cp {VPS_DATA_DIR}/bot.db {VPS_DATA_DIR}/bot_backup_pre_3000.db")
    print("  ✅ Backup created")

    # Step 2: Copy wallet_database.json to VPS
    print(f"\n[2] Copying wallet_database.json to VPS...")
    ok = scp_to_vps(str(WALLET_DB_PATH), f"{VPS_DATA_DIR}/wallet_database.json")
    if ok:
        print("  ✅ Copied")
    else:
        print("  ❌ Failed to copy!")
        sys.exit(1)

    # Step 3: Import new wallets into SQLite on VPS
    print(f"\n[3] Importing wallets into SQLite on VPS...")
    import_script = (
        "import json, sqlite3; "
        "db=json.load(open('data/wallet_database.json')); "
        "wallets=[v for v in db['wallets'].values() if v.get('status')=='ACTIVE']; "
        "conn=sqlite3.connect('data/bot.db'); "
        "c=conn.cursor(); "
        "existing=set(r[0] for r in c.execute('SELECT address FROM wallets').fetchall()); "
        "new=0; "
        "[("
        "c.execute('INSERT INTO wallets (address, status, realized_pnl_usd, win_rate, traded_token_count, source, wallet_type) "
        "VALUES (?,?,?,?,?,?,?)', "
        "(w['address'], 'ACTIVE', w.get('realized_pnl_usd',0), w.get('win_rate',0), "
        "w.get('traded_token_count',0), w.get('source',''), 'UNKNOWN')), "
        "new.__iadd__(1) if True else None"
        ") for w in wallets if w['address'] not in existing]; "  # This won't work with iadd
        "conn.commit(); "
        "total=c.execute('SELECT COUNT(*) FROM wallets WHERE status=\"ACTIVE\"').fetchone()[0]; "
        f"print(f'Imported to SQLite. Active in DB: {{total}}')"
    )
    # Simpler approach: use a standalone Python script on VPS
    import_py = '''
import json, sqlite3
db = json.load(open("data/wallet_database.json"))
wallets = [v for v in db["wallets"].values() if v.get("status") == "ACTIVE"]
conn = sqlite3.connect("data/bot.db")
c = conn.cursor()
existing = set(r[0] for r in c.execute("SELECT address FROM wallets").fetchall())
new = 0
for w in wallets:
    if w["address"] not in existing:
        c.execute(
            "INSERT INTO wallets (address, status, realized_pnl_usd, win_rate, traded_token_count, source, wallet_type) VALUES (?,?,?,?,?,?,?)",
            (w["address"], "ACTIVE", w.get("realized_pnl_usd", 0), w.get("win_rate", 0),
             w.get("traded_token_count", 0), w.get("source", ""), "UNKNOWN")
        )
        new += 1
conn.commit()
total = c.execute("SELECT COUNT(*) FROM wallets WHERE status='ACTIVE'").fetchone()[0]
print(f"Imported {new} new wallets. Total ACTIVE in SQLite: {total}")
conn.close()
'''
    # Write temp script, copy to VPS, run
    tmp_script = DATA_DIR / "_import_new.py"
    tmp_script.write_text(import_py)
    scp_to_vps(str(tmp_script), f"{VPS_BOT_DIR}/_import_new.py")
    result = ssh_cmd(f"cd {VPS_BOT_DIR} && python3 _import_new.py && rm _import_new.py", timeout=60)
    print(f"  {result}")
    tmp_script.unlink(missing_ok=True)

    # Step 4: Recreate Helius webhooks
    if not args.skip_webhooks:
        print(f"\n[4] Recreating Helius webhooks...")
        active_addresses = sorted(active_wallets.keys())
        from config.settings import HELIUS_WEBHOOK_URL
        asyncio.run(recreate_webhooks(active_addresses, HELIUS_WEBHOOK_URL))
    else:
        print(f"\n[4] Skipping webhook recreation (--skip-webhooks)")

    # Step 5: Restart bot
    print(f"\n[5] Restarting bot on VPS...")
    ssh_cmd("systemctl restart wayan-bot", timeout=15)
    import time
    time.sleep(3)
    status = ssh_cmd("systemctl is-active wayan-bot")
    print(f"  Bot status: {status}")

    print(f"\n{'='*60}")
    print(f"  DEPLOY COMPLETE")
    print(f"{'='*60}")
    print(f"  Active wallets: {active_count}")
    webhooks_needed = (active_count + ADDRESSES_PER_WEBHOOK - 1) // ADDRESSES_PER_WEBHOOK
    print(f"  Helius webhooks: {webhooks_needed}")
    print(f"  Bot status: {status}")


if __name__ == "__main__":
    main()
