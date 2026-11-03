import os
import sqlite3
import stat

import pytest

from awgbot.store import Store


def test_fresh_db_is_0600(tmp_path):
    path = str(tmp_path / "sub" / "db.sqlite")
    store = Store(path)
    assert os.path.exists(path)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    store.close()


def test_touch_user_is_idempotent(store):
    store.touch_user(1, "ivan")
    store.touch_user(1, "ivan_new")
    rows = store.conn.execute("SELECT * FROM users").fetchall()
    assert len(rows) == 1
    assert rows[0]["username"] == "ivan_new"


def test_request_lifecycle(store):
    req_id = store.create_request(7, "iphone")
    assert store.request(req_id)["status"] == "pending"
    assert len(store.pending_requests(7)) == 1
    store.decide_request(req_id, "approved", 42)
    row = store.request(req_id)
    assert row["status"] == "approved"
    assert row["decided_by"] == 42
    assert store.pending_requests() == []


def test_last_request_at(store):
    assert store.last_request_at(7) is None
    store.create_request(7, "a")
    assert store.last_request_at(7) is not None


def test_active_peers_and_used_ips(store):
    pid = store.add_peer(7, "iphone", "pub", "priv", "psk", "10.8.0.2")
    store.add_peer(7, "ipad", "pub2", "priv2", "psk2", "10.8.0.3")
    assert len(store.active_peers(7)) == 2
    assert store.used_ips() == {"10.8.0.2", "10.8.0.3"}
    store.revoke(pid)
    assert len(store.active_peers(7)) == 1
    assert store.used_ips() == {"10.8.0.3"}


def test_stats(store):
    store.touch_user(7, "u")
    store.create_request(7, "x")
    store.add_peer(7, "x", "p", "pv", "k", "10.8.0.2")
    stats = store.stats()
    assert stats == {"users": 1, "peers": 1, "pending": 1}


def test_audit_appends(store):
    store.audit(1, "issue", "x")
    store.audit(1, "revoke", "y")
    rows = store.conn.execute("SELECT action, detail FROM audit ORDER BY rowid").fetchall()
    assert [tuple(r) for r in rows] == [("issue", "x"), ("revoke", "y")]


def test_peer_from_row_reads_all_slots(store):
    store.add_peer(7, "iphone", "pub", "priv", "psk", "10.8.0.2")
    peer = store.active_peers(7)[0]
    assert (peer.tg_id, peer.device, peer.public_key, peer.ip) == (7, "iphone", "pub", "10.8.0.2")
