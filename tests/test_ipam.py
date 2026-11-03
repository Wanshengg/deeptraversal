import pytest

from awgbot import ipam


def test_pool_skips_server_and_reserved():
    hosts = ipam.pool("10.8.0.0/29", "10.8.0.1")
    assert "10.8.0.1" not in hosts
    assert hosts[0] == "10.8.0.2"
    assert "10.8.0.6" in hosts


def test_allocate_lowest_free():
    assert ipam.allocate("10.8.0.0/24", "10.8.0.1", set()) == "10.8.0.2"
    assert ipam.allocate("10.8.0.0/24", "10.8.0.1", {"10.8.0.2", "10.8.0.3"}) == "10.8.0.4"


def test_allocate_skips_reserved_ips():
    assert ipam.allocate("10.8.0.0/24", "10.8.0.1", set(), ["10.8.0.2"]) == "10.8.0.3"


def test_allocate_returns_none_when_exhausted():
    used = {"10.8.0.%d" % i for i in range(2, 7)}
    assert ipam.allocate("10.8.0.0/29", "10.8.0.1", used) is None


def test_allocate_accepts_cidr_suffixes():
    assert ipam.allocate("10.8.0.0/24", "10.8.0.1/24", {"10.8.0.2/32"}) == "10.8.0.3"


def test_bad_subnet_raises():
    with pytest.raises(ValueError):
        ipam.pool("10.8.0.0/33", "10.8.0.1")
