"""CLI wiring (small but real: setup.sh depends on --whoami)."""

from awgbot import __main__ as cli


def test_whoami_prints_bot_username(cfg, monkeypatch, capsys):
    class FakeTelegram(object):
        def __init__(self, token, api_base=None):
            assert token == cfg.bot_token

        def get_me(self):
            return {"username": "deeptraversal_bot"}

    monkeypatch.setattr(cli, "Telegram", FakeTelegram)
    assert cli.cmd_whoami(cfg) == 0
    assert capsys.readouterr().out.strip() == "deeptraversal_bot"


def test_whoami_falls_back_to_first_name(cfg, monkeypatch, capsys):
    class FakeTelegram(object):
        def __init__(self, *args, **kwargs):
            pass

        def get_me(self):
            return {"first_name": "deeptraversal"}

    monkeypatch.setattr(cli, "Telegram", FakeTelegram)
    cli.cmd_whoami(cfg)
    assert capsys.readouterr().out.strip() == "deeptraversal"
