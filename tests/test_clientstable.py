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
    # Amnezia 3.1 spells it allowed_ips; a brand new table follows that
    assert json.loads(text)[0]["userData"]["allowed_ips"] == "10.8.1.2/32"


AWG31_TABLE = """[
    {
        "clientId": "ADMINKEY=",
        "userData": {
            "allowed_ips": "10.8.1.1/32",
            "clientName": "Admin [macOS 27.2]",
            "creationDate": "Thu Nov 5 10:49:30 2026"
        }
    }
]
"""


def test_upsert_follows_the_existing_allowed_ips_spelling():
    text = clientstable.upsert(AWG31_TABLE, "NEW=", "iphone", "10.8.1.9")
    entries = clientstable.parse(text)
    new = clientstable.find(entries, "NEW=")
    assert new["userData"]["allowed_ips"] == "10.8.1.9/32"
    assert "allowedIps" not in new["userData"]
    # the app's own entry is untouched, key spelling included
    assert entries[0]["userData"]["allowed_ips"] == "10.8.1.1/32"


def test_upsert_keeps_legacy_allowed_ips_camel_case_tables_consistent():
    text = clientstable.upsert(AMNEZIA_TABLE, "NEW=", "iphone", "10.8.1.9")
    new = clientstable.find(clientstable.parse(text), "NEW=")
    assert new["userData"]["allowedIps"] == "10.8.1.9/32"
    assert "allowed_ips" not in new["userData"]


def test_updating_an_entry_that_has_both_spellings_keeps_them_equal():
    both = json.dumps([{"clientId": "K=", "userData": {"allowedIps": "10.8.1.2/32",
                                                       "allowed_ips": "10.8.1.2/32"}}])
    entry = clientstable.find(clientstable.parse(
        clientstable.upsert(both, "K=", "iphone", "10.8.1.5")), "K=")
    assert entry["userData"]["allowedIps"] == "10.8.1.5/32"
    assert entry["userData"]["allowed_ips"] == "10.8.1.5/32"
