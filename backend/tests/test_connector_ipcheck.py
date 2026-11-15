import ipaddress

import pytest

from roracle.connectors import netguard
from roracle.connectors.netguard import Blocked, check_ip

# --- check_ip ---

# (address, a CIDR that covers it): every BLOCKED6 range is blocked by default, allowed via explicit CIDR
V6_BLOCKED = [
    ("::ffff:0:a00:1", "::ffff:0:0:0/96"),
    ("::ffff:10.0.0.1", "::ffff:10.0.0.0/104"),
    ("::ffff:127.0.0.1", "::ffff:127.0.0.0/104"),
    ("::ffff:8.8.8.8", "::ffff:8.8.8.8/128"),  # mapped is never trusted by default, even for public v4
    ("64:ff9b::808:808", "64:ff9b::/96"),
    ("64:ff9b:1::1", "64:ff9b:1::/48"),
    ("64:ff9b:1:0:a9:fea9:fe00:0", "64:ff9b:1::/48"),
    ("2002:808:808:1::1", "2002::/16"),
    ("2001:0:4136:e378:8000:63bf:3fff:fdfe", "2001::/32"),
    ("::8.8.8.8", "::8.8.8.8/128"),
    ("::7f00:1", "::/96"),
    ("100::1", "100::/64"),
    ("fc00::1", "fc00::/7"),
    ("::1", "::1/128"),
]


@pytest.mark.parametrize(("ip", "cidr"), V6_BLOCKED)
def test_blocked6_ranges_need_explicit_cidr(ip, cidr):
    with pytest.raises(Blocked):
        check_ip(ip, [])
    check_ip(ip, [cidr])


@pytest.mark.parametrize(
    "ip",
    ["127.0.0.1", "10.0.0.1", "192.168.1.1", "172.16.0.1", "100.64.0.1", "0.0.0.0", "169.254.169.254",
     "224.0.0.1", "fe80::1", "fe80::1%eth0", "ff02::1", "fd00:ec2::254", "100.100.100.200", "168.63.129.16"],
)  # fmt: skip
def test_non_public_blocked_without_allowlist(ip):
    with pytest.raises(Blocked):
        check_ip(ip, [])


@pytest.mark.parametrize("ip", ["8.8.8.8", "93.184.216.34", "2606:4700:4700::1111", "2606:4700::1111"])
def test_public_allowed(ip):
    check_ip(ip, [])


def test_allowlist_permits_private():
    check_ip("10.1.2.3", ["10.0.0.0/8"])
    check_ip(ipaddress.ip_address("fd00::5"), ["fd00::/8"])


@pytest.mark.parametrize(
    "ip",
    ["169.254.169.254", "169.254.1.1", "fe80::1", "fe80::1%eth0", "224.0.0.1", "ff02::1", "0.0.0.0", "::",
     "fd00:ec2::254", "100.100.100.200", "168.63.129.16", "192.0.0.192",
     "::ffff:169.254.169.254", "::ffff:100.100.100.200", "::ffff:224.0.0.1", "::ffff:0.0.0.0"],
)  # fmt: skip
def test_never_list_blocked_even_with_everything_allowlisted(ip):
    with pytest.raises(Blocked):
        check_ip(ip, ["0.0.0.0/0", "::/0"])


def test_check_url_normalises_idn_and_path():
    assert netguard.check_url("http://b\u00fccher.example/caf\u00e9?q=\u00e9") == (
        "http://xn--bcher-kva.example/caf%C3%A9?q=%C3%A9"
    )
    assert netguard.check_url("http://[::1]:8080/a%20b") == "http://[::1]:8080/a%20b"
    with pytest.raises(Blocked):
        netguard.check_url("http://exa mple.example:99999999/")
