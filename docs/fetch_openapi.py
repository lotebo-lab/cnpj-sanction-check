"""Download the official OpenAPI document of the Portal da Transparencia API.

Run: ../../.venv/bin/python docs/fetch_openapi.py
Saves the raw bytes next to this file, no parsing, no interpretation.
"""

import pathlib
import sys

import requests

URL = "https://api.portaldatransparencia.gov.br/v3/api-docs"
OUT = pathlib.Path(__file__).with_name("portal-openapi-2026-09-20.json")

try:
    response = requests.get(URL, timeout=60, headers={"Accept": "application/json"})
except Exception as exc:  # noqa: BLE001
    print(f"NETWORK REFUSED: {exc.__class__.__name__}: {exc}")
    sys.exit(1)

print(f"HTTP {response.status_code} bytes={len(response.content)}")
OUT.write_bytes(response.content)
print(f"saved {OUT}")
