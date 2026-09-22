"""Print, flat and unambiguous, every schema reachable from CeisDTO / CnepDTO.

Reads only the file downloaded by fetch_openapi.py. No network.
Run: ../../.venv/bin/python docs/dump_schemas.py
"""

import json
import pathlib

DOC = json.loads(
    pathlib.Path(__file__).with_name("portal-openapi-2026-09-20.json").read_text("utf-8")
)
SCHEMAS = DOC.get("components", {}).get("schemas", {})


def refs_of(prop):
    ref = prop.get("$ref") or (prop.get("items") or {}).get("$ref")
    return ref.rsplit("/", 1)[-1] if ref else ""


def collect(roots):
    order, queue = [], list(roots)
    while queue:
        name = queue.pop(0)
        if name in order or name not in SCHEMAS:
            continue
        order.append(name)
        for prop in (SCHEMAS[name].get("properties") or {}).values():
            ref = refs_of(prop)
            if ref:
                queue.append(ref)
    return order


for name in collect(["CeisDTO", "CnepDTO"]):
    schema = SCHEMAS[name]
    print(f"\n===== {name} (type={schema.get('type')})")
    for key, prop in (schema.get("properties") or {}).items():
        ref = refs_of(prop)
        kind = prop.get("type") or ""
        if prop.get("type") == "array":
            kind = f"array of {ref or (prop.get('items') or {}).get('type')}"
        elif ref:
            kind = ref
        print(f"    {key} : {kind}")
