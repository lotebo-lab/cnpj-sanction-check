"""Print the CEPIM and Acordos de Leniencia routes and their response schemas.

Reads only the file downloaded by fetch_openapi.py. No network.
Run: ../../.venv/bin/python docs/dump_new_routes.py
"""

import json
import pathlib

DOC = json.loads(
    pathlib.Path(__file__).with_name("portal-openapi-2026-09-20.json").read_text("utf-8")
)
PATHS = DOC.get("paths", {})
SCHEMAS = DOC.get("components", {}).get("schemas", {})

ROUTES = [
    "/api-de-dados/cepim",
    "/api-de-dados/cepim/{id}",
    "/api-de-dados/acordos-leniencia",
    "/api-de-dados/acordos-leniencia/{id}",
]


def refs_of(prop):
    ref = prop.get("$ref") or (prop.get("items") or {}).get("$ref")
    return ref.rsplit("/", 1)[-1] if ref else ""


roots = []
for route in ROUTES:
    print(f"\n########## {route}  present={route in PATHS}")
    spec = PATHS.get(route)
    if not spec:
        continue
    for method, op in spec.items():
        print(f"  method={method} summary={op.get('summary')!r} tags={op.get('tags')}")
        for param in op.get("parameters") or []:
            print(
                f"    param {param.get('name')!r} in={param.get('in')} "
                f"required={param.get('required')} desc={param.get('description')!r}"
            )
        for code, resp in (op.get("responses") or {}).items():
            for ctype, media in (resp.get("content") or {}).items():
                schema = media.get("schema") or {}
                ref = refs_of(schema)
                print(f"    response {code} {ctype} -> {schema.get('type')} ref={ref}")
                if ref:
                    roots.append(ref)


def collect(seed):
    order, queue = [], list(seed)
    while queue:
        name = queue.pop(0)
        if name in order or name not in SCHEMAS:
            continue
        order.append(name)
        for prop in (SCHEMAS[name].get("properties") or {}).values():
            r = refs_of(prop)
            if r:
                queue.append(r)
    return order


for name in collect(roots):
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
