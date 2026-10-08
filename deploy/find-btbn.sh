#!/bin/bash
set -euo pipefail
curl -fsSL "https://api.github.com/repos/BtbN/FFmpeg-Builds/releases?per_page=8" -o /tmp/btbn-releases.json
python3 <<'PY'
import json
rels = json.load(open("/tmp/btbn-releases.json"))
for r in rels:
    print("TAG", r.get("tag_name"), (r.get("published_at") or "")[:10])
    for a in r.get("assets", []):
        n = a["name"]
        if "linux64-gpl" in n and n.endswith(".tar.xz") and "shared" not in n:
            print(" ", n)
            print(" ", a["browser_download_url"])
PY
