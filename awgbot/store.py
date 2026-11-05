"""SQLite persistence: users, requests, peers, audit log.

The database holds client *private* keys (needed to re-send a config on /key), so it is
created with mode 0600 and must be treated as a secret. See README "Security".
"""

import os
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    tg_id      INTEGER PRIMARY KEY,
    username   TEXT,
    status     TEXT NOT NULL DEFAULT 'new',   -- new | approved | banned
    created_at INTEGER NOT NULL,
    note       TEXT
);
CREATE TABLE IF NOT EXISTS requests (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    tg_id      INTEGER NOT NULL,
    device     TEXT NOT NULL,
    status     TEXT NOT NULL,                 -- pending | approved | rejected
    created_at INTEGER NOT NULL,
    decided_by INTEGER,
    decided_at INTEGER
);
CREATE TABLE IF NOT EXISTS peers (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    tg_id         INTEGER NOT NULL,
    device        TEXT NOT NULL,
    public_key    TEXT NOT NULL,
    private_key   TEXT NOT NULL,
    preshared_key TEXT NOT NULL,
    ip            TEXT NOT NULL,
    created_at    INTEGER NOT NULL,
    revoked_at    INTEGER
);
CREATE INDEX IF NOT EXISTS peers_tg ON peers(tg_id, revoked_at);
CREATE TABLE IF NOT EXISTS audit (
    ts     INTEGER NOT NULL,
    actor  TEXT NOT NULL,
    action TEXT NOT NULL,
    detail TEXT
);
"""


def _now():
    return int(time.time())


class Peer(object):
    __slots__ = ("id", "tg_id", "device", "public_key", "private_key",
                 "preshared_key", "ip", "created_at", "revoked_at")

    def __init__(self, row):
        for slot in self.__slots__:
            setattr(self, slot, row[slot] if isinstance(row, sqlite3.Row) else None)

    @classmethod
    def from_row(cls, row):
        obj = cls.__new__(cls)
        for slot in cls.__slots__:
            setattr(obj, slot, row[slot])
        return obj

    def __repr__(self):
        return "<Peer %s %s %s>" % (self.tg_id, self.ip, self.public_key[:8])


class Store(object):
    def __init__(self, path):
        self.path = path
        directory = os.path.dirname(os.path.abspath(path))
        if directory and not os.path.isdir(directory):
            os.makedirs(directory)
        fresh = not os.path.exists(path)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        if fresh:
            os.chmod(path, 0o600)

    def close(self):
        self.conn.close()

    # --- users -----------------------------------------------------------------
    def touch_user(self, tg_id, username):
        self.conn.execute(
            "INSERT INTO users(tg_id, username, created_at) VALUES(?,?,?) "
            "ON CONFLICT(tg_id) DO UPDATE SET username=excluded.username",
            (tg_id, username, _now()),
        )
        self.conn.commit()

    def user(self, tg_id):
        return self.conn.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,)).fetchone()

    def peer(self, peer_id):
        row = self.conn.execute("SELECT * FROM peers WHERE id=?", (peer_id,)).fetchone()
        return Peer.from_row(row) if row and row["revoked_at"] is None else None

    def set_user_status(self, tg_id, status, note=None):
        self.conn.execute(
            "INSERT INTO users(tg_id, status, created_at, note) VALUES(?,?,?,?) "
            "ON CONFLICT(tg_id) DO UPDATE SET status=excluded.status, note=excluded.note",
            (tg_id, status, _now(), note),
        )
        self.conn.commit()

    # --- requests --------------------------------------------------------------
    def create_request(self, tg_id, device):
        cur = self.conn.execute(
            "INSERT INTO requests(tg_id, device, status, created_at) VALUES(?,?,'pending',?)",
            (tg_id, device, _now()),
        )
        self.conn.commit()
        return cur.lastrowid

    def request(self, req_id):
        return self.conn.execute("SELECT * FROM requests WHERE id=?", (req_id,)).fetchone()

    def pending_requests(self, tg_id=None):
        if tg_id is None:
            return self.conn.execute(
                "SELECT * FROM requests WHERE status='pending' ORDER BY id"
            ).fetchall()
        return self.conn.execute(
            "SELECT * FROM requests WHERE status='pending' AND tg_id=? ORDER BY id", (tg_id,)
        ).fetchall()

    def last_request_at(self, tg_id):
        row = self.conn.execute(
            "SELECT MAX(created_at) AS ts FROM requests WHERE tg_id=?", (tg_id,)
        ).fetchone()
        return row["ts"]

    def decide_request(self, req_id, status, actor):
        self.conn.execute(
            "UPDATE requests SET status=?, decided_by=?, decided_at=? WHERE id=?",
            (status, actor, _now(), req_id),
        )
        self.conn.commit()

    # --- peers -----------------------------------------------------------------
    def add_peer(self, tg_id, device, public_key, private_key, preshared_key, ip):
        cur = self.conn.execute(
            "INSERT INTO peers(tg_id, device, public_key, private_key, preshared_key, ip,"
            " created_at) VALUES(?,?,?,?,?,?,?)",
            (tg_id, device, public_key, private_key, preshared_key, ip, _now()),
        )
        self.conn.commit()
        return cur.lastrowid

    def active_peers(self, tg_id=None):
        if tg_id is None:
            rows = self.conn.execute(
                "SELECT * FROM peers WHERE revoked_at IS NULL ORDER BY id"
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM peers WHERE revoked_at IS NULL AND tg_id=? ORDER BY id", (tg_id,)
            ).fetchall()
        return [Peer.from_row(r) for r in rows]

    def used_ips(self):
        return {row["ip"] for row in self.conn.execute(
            "SELECT ip FROM peers WHERE revoked_at IS NULL")}

    def revoke(self, peer_id):
        self.conn.execute("UPDATE peers SET revoked_at=? WHERE id=?", (_now(), peer_id))
        self.conn.commit()

    # --- audit -----------------------------------------------------------------
    def audit(self, actor, action, detail=""):
        self.conn.execute(
            "INSERT INTO audit(ts, actor, action, detail) VALUES(?,?,?,?)",
            (_now(), str(actor), action, detail),
        )
        self.conn.commit()

    def stats(self):
        row = self.conn.execute(
            "SELECT (SELECT COUNT(*) FROM users) AS users,"
            " (SELECT COUNT(*) FROM peers WHERE revoked_at IS NULL) AS peers,"
            " (SELECT COUNT(*) FROM requests WHERE status='pending') AS pending"
        ).fetchone()
        return {"users": row["users"], "peers": row["peers"], "pending": row["pending"]}
