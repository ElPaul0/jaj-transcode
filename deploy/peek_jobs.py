import json
import urllib.request
from collections import Counter

with urllib.request.urlopen("http://127.0.0.1:8791/api/session") as r:
    d = json.load(r)
jobs = d.get("jobs") or []
print("states", dict(Counter(j["state"] for j in jobs)))
for j in jobs:
    if j["state"] != "error":
        continue
    name = j["source"].split("/")[-1]
    err = (j.get("error") or "").replace("\n", " ")[:220]
    print("ERR", name, "|", err)
