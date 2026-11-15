import ipaddress

import pytest

from roracle.connectors import netguard
from roracle.connectors.netguard import Blocked, check_ip

# --- check_ip ---


@pytest.mark.parametrize(
    "ip",
    ["127.0.0.1", "10.0.0.1", "192.168.1.1", "172.16.0.1", "100.64.0.1", "0.0.0.0", "::1", "fc00::1", "169.254.169.254",
     "fe80::1", "fe80::1%eth0", "224.0.0.1", "ff02::1", "::ffff:10.0.0.1", "::ffff:127.0.0.1",
     "2002:7f00:1::1", "2002:0a00:0001::", "64:ff9b::a00:1", "::7f00:1",
     "64:ff9b:1::a00:1", "fd00:ec2::254", "100.100.100.200", "168.63.129.16"],
)  # fmt: skip
def test_non_public_blocked_without_allowlist(ip):
    with pytest.raises(Blocked):
        check_ip(ip, [])


@pytest.mark.parametrize("ip", ["8.8.8.8", "93.184.216.34", "2606:4700:4700::1111", "::ffff:8.8.8.8"])
def test_public_allowed(ip):
    check_ip(ip, [])


def test_allowlist_permits_private_but_never_link_local_multicast():
    check_ip("10.1.2.3", ["10.0.0.0/8"])
    check_ip(ipaddress.ip_address("fd00::5"), ["fd00::/8"])
    for ip, cidr in [("169.254.169.254", "169.254.0.0/16"), ("fe80::1", "fe80::/10"),
                     ("224.0.0.1", "224.0.0.0/4"), ("0.0.0.0", "0.0.0.0/8"),
                     ("::ffff:169.254.169.254", "::ffff:0:0/96"), ("fd00:ec2::254", "fd00::/8"),
                     ("100.100.100.200", "100.64.0.0/10"), ("168.63.129.16", "168.63.129.16/32"),
                     ("::ffff:100.100.100.200", "100.64.0.0/10"), ("64:ff9b:1::a9fe:a9fe", "64:ff9b:1::/48")]:  # fmt: skip
        with pytest.raises(Blocked):
            check_ip(ip, [cidr])


def test_wrapped_v4_judged_as_inner_and_loopback_not_unwrapped():
    check_ip("::1", ["::1/128"])  # not unwrapped to 0.0.0.1
    check_ip("::ffff:127.0.0.1", ["127.0.0.0/8"])
    check_ip("::ffff:10.0.0.1", ["10.0.0.0/8"])
    with pytest.raises(Blocked):
        check_ip("::1", [])
    with pytest.raises(Blocked):
        check_ip("::", ["::/0"])


def test_check_url_normalises_idn_and_path():
    assert netguard.check_url("http://b\u00fccher.example/caf\u00e9?q=\u00e9") == (
        "http://xn--bcher-kva.example/caf%C3%A9?q=%C3%A9"
    )
    assert netguard.check_url("http://[::1]:8080/a%20b") == "http://[::1]:8080/a%20b"
    with pytest.raises(Blocked):
        netguard.check_url("http://exa mple.example:99999999/")
