"""Daily read-only reconciliation. CRM writes are refused."""

from __future__ import annotations

import os

from src.crm_refresh import refresh_accounts
from src.run_phase1 import main as reconcile_saved_accounts


def main() -> None:
    if os.environ.get("CRM_WRITES_ENABLED", "").strip().lower() == "true":
        raise SystemExit("Daily reconciliation refuses to run while CRM writes are enabled")
    refresh_accounts()
    reconcile_saved_accounts()
    print("crm_writes_enabled=false")
    print("crm_requests=GET")


if __name__ == "__main__":
    main()
