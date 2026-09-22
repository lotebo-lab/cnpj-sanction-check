"""Print the security scheme, the CEIS/CNEP parameter descriptions and the {id} routes.

Reads only the downloaded file. No network.
"""

import json
import pathlib

DOC = json.loads(
    pathlib.Path(__file__).with_name("portal-openapi-2026-09-20.json").read_text("utf-8")
)

print("== servers:", json.dumps(DOC.get("servers"), ensure_ascii=False))
print("== securitySchemes:")
print(
    json.dumps(
        DOC.get("components", {}).get("securitySchemes"), ensure_ascii=False, indent=2
    )
)

for path, item in DOC.get("paths", {}).items():
    if "ceis" in path or "cnep" in path:
        get = item.get("get", {})
        print(f"\n== {path}")
        print("   summary:", get.get("summary"))
        print("   security:", json.dumps(get.get("security"), ensure_ascii=False))
        for p in get.get("parameters", []):
            print(
                f"   param {p.get('name')} in={p.get('in')} required={p.get('required')} "
                f"desc={p.get('description')}"
            )
        ok = get.get("responses", {}).get("200", {})
        print("   200 schema:", json.dumps(ok.get("content"), ensure_ascii=False))
