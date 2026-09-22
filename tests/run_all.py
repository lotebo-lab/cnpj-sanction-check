"""Runs every offline test file of this Actor in one command.

pytest is not installed in the lab sandbox and `pip install` has no network
there, so each test file is its own runner and this script just calls them all.
Nothing here touches the network: the HTTP transport and the Apify Actor are
both replaced by fakes inside the test files.

    .venv/bin/python negocios/actor-idoneidade-cnpj/tests/run_all.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FILES = (
    "test_schema_contract.py",
    "test_rules.py",
    "test_portal_client.py",
    "test_charging.py",
    "test_unmapped_keys_summary.py",
    "test_summary_row.py",
)


def main() -> int:
    failed: list[str] = []
    for name in FILES:
        # flush=True, or our own lines land after the child's output.
        print(f"===== {name}", flush=True)
        result = subprocess.run([sys.executable, str(HERE / name)], check=False)
        if result.returncode != 0:
            failed.append(name)
        print(flush=True)
    if failed:
        print(f"FAILED: {', '.join(failed)}")
        return 1
    print(f"ALL GREEN: {len(FILES)} test files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
