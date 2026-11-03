"""Tiny IP address pool for peer allocation."""

import ipaddress


def pool(subnet, server_ip, reserved=()):
    """Return every usable host address of `subnet`, lowest first, minus reservations."""
    net = ipaddress.ip_network(subnet, strict=False)
    reserved = {str(ipaddress.ip_interface(ip).ip) for ip in reserved}
    reserved.add(str(ipaddress.ip_interface(server_ip).ip))
    out = []
    for host in net.hosts():
        if str(host) in reserved:
            continue
        out.append(str(host))
    if net.prefixlen >= 31:  # /31 and /32 have no "hosts" in the strict sense
        out = [str(h) for h in net if str(h) not in reserved]
    return out


def allocate(subnet, server_ip, used, reserved=()):
    """First free address, or None when the pool is exhausted."""
    used = {str(ipaddress.ip_interface(ip).ip) for ip in used}
    for candidate in pool(subnet, server_ip, reserved):
        if candidate not in used:
            return candidate
    return None
