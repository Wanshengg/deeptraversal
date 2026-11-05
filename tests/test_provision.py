import base64
import os
import stat

import pytest

from awgbot import clientconf
from awgbot.provision import ProvisionError, Provisioner, safe_device_name


def test_issue_creates_peer_conf_and_export(prov, store, backend, cfg):
    peer, conf, filename = prov.issue(100, "iphone")
    assert peer.ip == "10.8.0.2"
    assert filename == "amnezia-iphone-10.8.0.2.conf"
    assert "Address = 10.8.0.2/32" in conf
    assert "Endpoint = vpn.example.com:51820" in conf
    # obfuscation params from the (mock) server config are copied into the client file
    assert "Jc = 4" in conf and "H1 = 1234567" in conf
    assert "PrivateKey = %s" % peer.private_key in conf
    # keys are consistent: public key must be derivable from the stored private key
    from awgbot import keys
    assert keys.public_key(peer.private_key) == peer.public_key
    assert len(base64.b64decode(peer.preshared_key)) == 32
    # backend knows the peer
    assert backend.live_peers() == [peer.public_key]
    # export file is written 0600
    export = os.path.join(prov.export_dir(), filename)
    assert os.path.exists(export)
    assert stat.S_IMODE(os.stat(export).st_mode) == 0o600


def test_second_issue_adds_another_key(prov, store):
    first, _, _ = prov.issue(100, "iphone")
    second, _, _ = prov.issue(100, "laptop")
    active = store.active_peers(100)
    assert sorted(p.id for p in active) == sorted([first.id, second.id])
    assert first.ip != second.ip                      # свой адрес у каждого устройства
    assert store.used_ips() == {first.ip, second.ip}


def test_same_device_name_gets_a_suffix(prov, store):
    prov.issue(100, "iphone")
    again, _, _ = prov.issue(100, "iphone")
    third, _, _ = prov.issue(100, "iphone")
    assert [again.device, third.device] == ["iphone-2", "iphone-3"]
    names = {p.device for p in store.active_peers(100)}
    assert len(names) == 3


def test_peer_limit_raises_instead_of_silently_replacing(cfg, store, backend):
    from awgbot.provision import Provisioner, ProvisionError
    cfg.max_peers_per_user = 2
    prov = Provisioner(cfg, store, backend)
    prov.issue(100, "a")
    prov.issue(100, "b")
    with pytest.raises(ProvisionError) as exc:
        prov.issue(100, "c")
    assert "удалите" in str(exc.value).lower()
    assert len(store.active_peers(100)) == 2          # ничего не отозвано


def test_limit_zero_means_unlimited(cfg, store, backend):
    from awgbot.provision import Provisioner
    cfg.max_peers_per_user = 0
    cfg.subnet = "10.8.0.0/24"
    prov = Provisioner(cfg, store, backend)
    for i in range(6):
        prov.issue(100, "dev%s" % i)
    assert len(store.active_peers(100)) == 6


def test_replace_mode_still_revokes_old_keys(cfg, store, backend):
    from awgbot.provision import Provisioner
    prov = Provisioner(cfg, store, backend)
    first, _, _ = prov.issue(100, "iphone")
    prov.issue(100, "laptop")
    only, _, _ = prov.issue(100, "desktop", replace=True)
    active = store.active_peers(100)
    assert [p.id for p in active] == [only.id]
    assert active[0].ip == first.ip                   # адрес переиспользован


def test_resend_peer_targets_one_device(prov, store):
    first, _, _ = prov.issue(100, "iphone")
    second, _, _ = prov.issue(100, "laptop")
    delivery = prov.resend_peer(second.id, 100)
    assert delivery.peer.id == second.id
    assert "Address = %s/32" % second.ip in delivery.conf
    assert prov.resend_peer(first.id, 999) is None     # чужой ключ не отдаём
    assert prov.resend_peer(123456, 100) is None


def test_revoke_peer_removes_only_that_device(prov, store, backend):
    first, _, _ = prov.issue(100, "iphone")
    second, _, _ = prov.issue(100, "laptop")
    revoked = prov.revoke_peer(first.id, 100)
    assert revoked.id == first.id
    assert [p.id for p in store.active_peers(100)] == [second.id]
    assert backend.clients_in_app() == {second.public_key}
    assert prov.revoke_peer(second.id, 999) is None    # чужой ключ не трогаем
    assert len(store.active_peers(100)) == 1


def test_pool_exhaustion_raises(prov, store):
    for i in range(5):                       # /29 has 6 hosts, one is the server
        prov.issue(1000 + i, "dev%s" % i)
    with pytest.raises(ProvisionError) as exc:
        prov.issue(2000, "too-many")
    assert "exhausted" in str(exc.value)
    assert not store.active_peers(2000)


def test_revoke_frees_address_and_updates_backend(prov, store, backend):
    peer, _, _ = prov.issue(100, "iphone")
    assert prov.revoke_all(100) == 1
    assert store.active_peers(100) == []
    assert backend.live_peers() == []
    assert store.used_ips() == set()
    again, _, _ = prov.issue(100, "iphone")
    assert again.ip == peer.ip


def test_resend_returns_existing_config(prov):
    peer, conf, _ = prov.issue(100, "iphone")
    result = prov.resend(100)
    assert result is not None
    _, again, filename = result
    assert again == conf
    assert filename.endswith(".conf")


def test_issue_rolls_back_when_backend_fails(cfg, store, backend, prov):
    from awgbot.awg import BackendError

    def boom(peers):
        raise BackendError("syncconf failed")

    backend.sync = boom
    with pytest.raises(BackendError):
        prov.issue(100, "iphone")
    assert store.active_peers(100) == []


def test_audit_log_is_written(prov, store):
    prov.issue(100, "iphone")
    rows = store.conn.execute("SELECT action FROM audit").fetchall()
    assert [r[0] for r in rows] == ["issue"]


def test_allocation_skips_addresses_of_hand_made_peers(prov, store, backend):
    # a server that already had peers before the bot: .2 and .3 are taken outside the DB
    backend.seed(existing_ips=["10.8.0.2", "10.8.0.3"])
    peer, _, _ = prov.issue(100, "iphone")
    assert peer.ip == "10.8.0.4"
    assert store.used_ips() == {"10.8.0.4"}


def test_allocation_still_recycles_revoked_addresses(prov, store, backend):
    backend.seed(existing_ips=["10.8.0.2"])
    first, _, _ = prov.issue(100, "iphone")
    prov.revoke_all(100)
    second, _, _ = prov.issue(101, "laptop")
    assert first.ip == second.ip == "10.8.0.3"


def test_issue_lists_peer_in_amnezia_client_table(prov, backend):
    peer, _, _ = prov.issue(100, "iphone")
    assert backend.clients_in_app() == {peer.public_key}
    entry = backend._state["clients"][0]["userData"]
    assert entry["clientName"] == "iphone"
    assert entry["allowed_ips"] == "10.8.0.2/32"      # Amnezia 3.1 spelling
    assert "creationDate" in entry


def test_revoke_drops_peer_from_the_app_table(prov, backend):
    peer, _, _ = prov.issue(100, "iphone")
    prov.revoke_all(100)
    assert backend.clients_in_app() == set()


def test_reissue_replaces_the_app_entry(cfg, store, backend):
    from awgbot.provision import Provisioner
    prov = Provisioner(cfg, store, backend)
    first, _, _ = prov.issue(100, "iphone")
    second, _, _ = prov.issue(100, "laptop", replace=True)
    assert backend.clients_in_app() == {second.public_key}
    assert first.public_key not in backend.clients_in_app()


def test_issue_survives_a_broken_client_table(prov, backend):
    backend.register_client = lambda peer: False       # e.g. no clientsTable on the server
    result = prov.issue(100, "iphone")
    assert result.peer.ip == "10.8.0.2"                # issuance must not fail


def test_sync_clients_table_readds_entries_dropped_by_the_app(prov, store, backend):
    first, _, _ = prov.issue(100, "iphone")
    second, _, _ = prov.issue(101, "ipad")
    backend._state["clients"] = []                     # the AmneziaVPN app rewrote the file
    assert backend.clients_in_app() == set()
    assert prov.sync_clients_table() == 2
    assert backend.clients_in_app() == {first.public_key, second.public_key}


def test_sync_clients_table_is_idempotent(prov, backend):
    prov.issue(100, "iphone")
    assert prov.sync_clients_table() == 0              # nothing to do
    assert prov.sync_clients_table() == 0


# --- prune: peers deleted outside the bot (e.g. in the AmneziaVPN app) ----------------
def test_prune_drops_peer_removed_from_the_interface(prov, store, backend):
    peer, _, _ = prov.issue(100, "iphone")
    backend._state["peers"] = []                     # deleted in the app / by hand
    result = prov.prune(force=True)                  # all peers gone → needs force
    assert [p.id for p in result.removed] == [peer.id]
    assert store.active_peers(100) == []
    assert backend.live_peers() == []                # managed block cleaned by sync
    assert backend.clients_in_app() == set()         # gone from the app's Users list too
    actions = [r[0] for r in store.conn.execute("SELECT action FROM audit ORDER BY rowid")]
    assert actions == ["issue", "prune"]


def test_prune_keeps_healthy_peers(prov, store, backend):
    prov.issue(100, "iphone")
    result = prov.prune()
    assert result.removed == []
    assert len(store.active_peers(100)) == 1


def test_prune_reports_hand_made_peers_without_touching_them(prov, store, backend):
    prov.issue(100, "iphone")
    backend._state["peers"].append({"public_key": "MANUAL=", "preshared_key": "",
                                    "ip": "10.8.0.9", "tg_id": 0})
    result = prov.prune()
    assert result.removed == []
    assert result.unknown_live == ["MANUAL="]


def test_prune_refuses_when_interface_looks_down(prov, store, backend):
    from awgbot.provision import ProvisionError
    prov.issue(100, "iphone")
    backend._state["peers"] = []
    with pytest.raises(ProvisionError) as exc:
        prov.prune(force=False)
    assert "--force" in str(exc.value)
    assert len(store.active_peers(100)) == 1         # nothing was revoked


def test_prune_force_works_when_interface_really_is_empty(prov, store, backend):
    prov.issue(100, "iphone")
    backend._state["peers"] = []
    result = prov.prune(force=True)
    assert len(result.removed) == 1
    assert store.active_peers(100) == []


def test_prune_summary_is_human_readable(prov, store, backend):
    peer, _, _ = prov.issue(100, "iphone")
    backend._state["peers"] = []
    text = prov.prune(force=True).summary()
    assert "проверено пиров" in text and "iphone" in text and peer.ip in text


@pytest.mark.parametrize("raw,expected", [
    ("iphone", "iphone"),
    ("../../etc/passwd", "etcpasswd"),
    ("my laptop!", "mylaptop"),
    ("", "device"),
    ("....", "device"),
    ("a" * 80, "a" * 32),
])
def test_safe_device_name_strips_paths(raw, expected):
    assert safe_device_name(raw) == expected


def test_server_pubkey_used_verbatim_in_conf(prov, cfg):
    _, conf, _ = prov.issue(100, "iphone")
    assert "PublicKey = %s" % cfg.server_public_key in conf
    assert clientconf.MANAGED_BEGIN not in conf
