import json
from urllib.request import urlopen

with urlopen("http://127.0.0.1:8791/api/session") as r:
    d = json.load(r)
print(d.get("savings"))
