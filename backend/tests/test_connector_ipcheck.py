import ipaddress

import pytest

from roracle.connectors import netguard
from roracle.connectors.netguard import Blocked, check_ip

# --- check_ip ---

# Non-wrapper BLOCKED6 ranges: blocked by default, allowed via an explicit covering CIDR.
V6_BLOCKED = [("100::1", "100::/64"), ("fc00::1", "fc00::/7")]
# Wrapper forms with a PUBLIC inner IPv4 (8.8.8.8): blocked by default, allowed via the /96 translator prefix.
V6_WRAPPED = [
    ("::ffff:0:808:808", "::ffff:0:0:0/96"),
    ("64:ff9b::808:808", "64:ff9b::/96"),
    ("64:ff9b:1::808:808", "64:ff9b:1::/96"),
    ("::808:808", "::/96"),
]


@pytest.mark.parametrize(("ip", "cidr"), V6_BLOCKED + V6_WRAPPED)
def test_blocked6_ranges_need_explicit_cidr(ip, cidr):
    with pytest.raises(Blocked):
        check_ip(ip, [])
    check_ip(ip, [cidr])


@pytest.mark.parametrize(("ip", "cidr"), V6_WRAPPED)
def test_translator_inner_ipv4_is_vetted(ip, cidr):
    base = ip.rsplit(":", 2)[0]
    meta, loop, priv = (f"{base}:{h}" for h in ("a9fe:a9fe", "7f00:1", "a00:1"))
    with pytest.raises(Blocked):
        check_ip(meta, [cidr, "0.0.0.0/0"])  # metadata/link-local inner: never
    for addr, v4 in ((loop, "127.0.0.0/8"), (priv, "10.0.0.0/8")):
        with pytest.raises(Blocked):
            check_ip(addr, [cidr])  # non-public inner, v4 range not allowlisted
        check_ip(addr, [cidr, v4])
        with pytest.raises(Blocked):
            check_ip(addr, [v4])  # the /96 prefix itself is not allowlisted


def test_6to4_and_teredo_never_allowed_even_if_listed():
    for ip in ("2002:808:808:1::808:808", "2001:0:4136:e378:8000:63bf:f7f7:f7f7"):
        with pytest.raises(Blocked):
            check_ip(ip, ["::/0", "0.0.0.0/0", "2002::/16", "2001::/32", ip + "/128"])


def test_mapped_judged_by_inner_ipv4():
    check_ip("::ffff:8.8.8.8", [])
    with pytest.raises(Blocked):
        check_ip("::ffff:10.0.0.1", [])
    check_ip("::ffff:10.0.0.1", ["10.0.0.0/8"])
    with pytest.raises(Blocked):
        check_ip(
            "::ffff:127.0.0.1", ["::ffff:0:0/96"]
        )  # a v6 allowlist entry does not vouch for the inner v4
    check_ip("::ffff:127.0.0.1", ["127.0.0.0/8"])
    with pytest.raises(Blocked):
        check_ip("::ffff:169.254.169.254", ["::/0", "0.0.0.0/0"])


def test_loopback_v6_and_embedding_regressions():
    for ip in (
        "::1",
        "::",
        "64:ff9b:1:a9fe:a9:fe00:808:808",
        "2002:808:808:1::1",
        "::8.8.8.8",
        "64:ff9b::a00:1",
    ):
        with pytest.raises(Blocked):
            check_ip(ip, [])
    with pytest.raises(Blocked):
        check_ip(
            "2002:a9fe:a9fe::1", ["2002:a9fe:a9fe::/96"]
        )  # 6to4 of 169.254.169.254 (and unrelated low bits)
    with pytest.raises(Blocked):
        check_ip("64:ff9b:1:0:a9:fea9:fe00:0", ["64:ff9b:1::/96"])


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
     "::ffff:169.254.169.254", "::ffff:100.100.100.200", "::ffff:224.0.0.1", "::ffff:0.0.0.0",
     "64:ff9b::a9fe:a9fe"],
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
