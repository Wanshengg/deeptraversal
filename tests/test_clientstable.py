"""Amnezia's clientsTable — the only thing the app's "Users" screen reads."""

import json

from awgbot import clientstable

AMNEZIA_TABLE = """[
    {
        "clientId": "zNcRrwraxiy/tTF0tpSHDW8ij46gr1t8bmPVEvyUBFE=",
        "userData": {
            "allowedIps": "10.8.1.1/32",
            "clientName": "Admin [macOS 27.2]",
            "creationDate": "Tue Nov 3 12:46:52 2026",
            "dataReceived": "216.33 MiB",
            "dataSent": "10.37 MiB",
            "latestHandshake": "10m, 5s ago"
        }
    },
    {
        "clientId": "cABeiHBWFFX8A3J5JzAzMAZiSCXKh40D6Zhw6hsxghc=",
        "userData": {
            "clientName": "kostya",
            "creationDate": "Tue Nov 3 13:05:32 2026"
        }
    }
]
"""


def test_parse_tolerates_junk():
    assert clientstable.parse("") == []
    assert clientstable.parse("not json") == []
    assert clientstable.parse('{"clientId": "x"}') == []      # not a list
    assert clientstable.parse('[{"noId": 1}]') == []
    assert len(clientstable.parse(AMNEZIA_TABLE)) == 2


def test_upsert_appends_and_keeps_existing_entries():
    text = clientstable.upsert(AMNEZIA_TABLE, "NEWKEY=", "iphone", "10.8.1.8",
                               ts=1762000000)
    entries = clientstable.parse(text)
    assert len(entries) == 3
    assert entries[0]["userData"]["clientName"] == "Admin [macOS 27.2]"   # untouched
    assert entries[0]["userData"]["dataReceived"] == "216.33 MiB"          # stats kept
    new = clientstable.find(entries, "NEWKEY=")
    assert new["userData"]["clientName"] == "iphone"
    assert new["userData"]["allowedIps"] == "10.8.1.8/32"
    assert "creationDate" in new["userData"]


def test_upsert_updates_in_place_after_reissue():
    first = clientstable.upsert(AMNEZIA_TABLE, "K=", "old-name", "10.8.1.8")
    second = clientstable.upsert(first, "K=", "new-name", "10.8.1.9")
    entries = clientstable.parse(second)
    assert len(entries) == 3                     # no duplicate
    entry = clientstable.find(entries, "K=")
    assert entry["userData"]["clientName"] == "new-name"
    assert entry["userData"]["allowedIps"] == "10.8.1.9/32"


def test_remove_drops_only_that_client():
    text = clientstable.upsert(AMNEZIA_TABLE, "K=", "iphone", "10.8.1.8")
    text = clientstable.remove(text, "K=")
    entries = clientstable.parse(text)
    assert len(entries) == 2
    assert clientstable.ids(text) == {"zNcRrwraxiy/tTF0tpSHDW8ij46gr1t8bmPVEvyUBFE=",
                                     "cABeiHBWFFX8A3J5JzAzMAZiSCXKh40D6Zhw6hsxghc="}


def test_creation_date_matches_amnezia_shape():
    import re
    import time

    ts = 1762000000
    value = clientstable.creation_date(ts)
    assert re.match(r"^[A-Z][a-z]{2} [A-Z][a-z]{2} \d{1,2} \d{2}:\d{2}:\d{2} \d{4}$", value)
    st = time.localtime(ts)
    assert value == "%s %s %d %s %d" % (time.strftime("%a", st), time.strftime("%b", st),
                                        st.tm_mday, time.strftime("%H:%M:%S", st), st.tm_year)
    # no zero padding on the day, exactly like Amnezia writes it
    assert " 0" not in value.split(" ")[2]


def test_dumps_round_trips():
    entries = clientstable.parse(AMNEZIA_TABLE)
    assert clientstable.parse(clientstable.dumps(entries)) == entries
    assert clientstable.dumps([]).strip() == "[]"


def test_empty_table_gets_first_client():
    text = clientstable.upsert("", "K=", "laptop", "10.8.1.2")
    assert json.loads(text)[0]["clientId"] == "K="
