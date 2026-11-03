"""AmneziaVPN's client table.

The app's "Users" screen does **not** read the AWG config: it reads
`/opt/amnezia/awg/clientsTable` (next to `awg0.conf`, inside the container) — a JSON array

    [{"clientId": "<peer public key>",
      "userData": {"clientName": "...", "allowedIps": "10.8.1.8/32",
                   "creationDate": "Tue Nov 3 12:46:52 2026",
                   "dataReceived": "...", "dataSent": "...", "latestHandshake": "..."}}]

Peers created outside the app (by this bot) exist on the wire but have no entry here, so the
app cannot show them at all. We keep the file in sync: upsert on issue, drop on revoke.
Other entries are preserved verbatim — the file is shared with clients created by the app.
"""

import json
import time


def parse(text):
    if not text or not text.strip():
        return []
    try:
        data = json.loads(text)
    except ValueError:
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict) and item.get("clientId")]


def dumps(entries):
    return json.dumps(entries, indent=4, ensure_ascii=False) + "\n"


def creation_date(ts=None):
    """Same shape Amnezia uses: 'Tue Nov 3 12:46:52 2026' (day not zero-padded)."""
    st = time.localtime(ts if ts is not None else time.time())
    return "%s %s %d %s %d" % (time.strftime("%a", st), time.strftime("%b", st), st.tm_mday,
                               time.strftime("%H:%M:%S", st), st.tm_year)


def find(entries, client_id):
    for entry in entries:
        if entry.get("clientId") == client_id:
            return entry
    return None


def upsert(text, client_id, name, ip=None, ts=None):
    """Add or update one client, keeping every other entry untouched."""
    entries = parse(text)
    entry = find(entries, client_id)
    if entry is None:
        entry = {"clientId": client_id, "userData": {}}
        entries.append(entry)
    user = entry.setdefault("userData", {})
    if name:
        user["clientName"] = name
    if ip:
        user["allowedIps"] = ip if "/" in ip else "%s/32" % ip
    user.setdefault("creationDate", creation_date(ts))
    return dumps(entries)


def remove(text, client_id):
    entries = [e for e in parse(text) if e.get("clientId") != client_id]
    return dumps(entries)


def ids(text):
    return {e["clientId"] for e in parse(text)}


def name_of(text, client_id):
    entry = find(parse(text), client_id)
    return ((entry or {}).get("userData") or {}).get("clientName")
