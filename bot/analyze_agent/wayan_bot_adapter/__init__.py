"""WAYNE_PIRATE integration layer for the accumulation detector.

On import, ensures the parent bot/analyze_agent/ directory is on sys.path
so the standalone agent modules (agent_config, data_fetcher, detector, …)
resolve from any submodule. This runs before handlers.py or
scheduler_jobs.py attempt their bare-name imports.

Exposed entry points:
  - handlers.acc_router          → aiogram 3 Router (register in dp)
  - scheduler_jobs.register_accumulation_jobs(scheduler) → hook into bot's APScheduler
  - migrate.apply_migration(db_path) → create accumulation_* tables
"""
import sys
from pathlib import Path

_agent_dir = str(Path(__file__).resolve().parent.parent)
if _agent_dir not in sys.path:
    sys.path.insert(0, _agent_dir)
