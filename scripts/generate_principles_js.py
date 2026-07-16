#!/usr/bin/env python3
"""Generate browser metadata from the authoritative vendored principles.json."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
catalog = json.loads((ROOT / "principles.json").read_text(encoding="utf-8"))
output = {}
for principle in catalog["principles"]:
    checks = principle.get("checks", [])
    check_summary = " ".join(
        f'{check["id"]}: {check.get("detectableVia", check["summary"])}'
        for check in checks
    )
    output[principle["id"]] = {
        "title": principle["title"],
        "status": "",
        "desc": principle["description"],
        "how": f'{len(checks)} authoritative checks from principles.json. {check_summary}',
    }

text = "var PRINCIPLES=" + json.dumps(output, ensure_ascii=False, separators=(",", ":")) + ";\n"
(ROOT / "principles.js").write_text(text, encoding="utf-8")
print(f"Generated principles.js for {len(output)} principles and {sum(len(p.get('checks', [])) for p in catalog['principles'])} checks")
