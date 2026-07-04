# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""Tests for generators/nebula.py."""
import pytest

from backend.generators import nebula


CERT = "-----BEGIN NEBULA CERTIFICATE-----\nabc\n-----END NEBULA CERTIFICATE-----\n"
KEY  = "-----BEGIN NEBULA ED25519 PRIVATE KEY-----\nabc\n-----END NEBULA ED25519 PRIVATE KEY-----\n"
CA   = "-----BEGIN NEBULA CERTIFICATE-----\ndef\n-----END NEBULA CERTIFICATE-----\n"


def _nb(**overrides):
    base = {
        "enabled": True,
        "listen_port": 4242,
        "lighthouse_hosts": [],
        "static_host_map": {},
        "cert_pem": CERT,
        "key_pem": KEY,
        "ca_pem": CA,
        "firewall_inbound": [],
        "firewall_outbound": [{"port": "any", "proto": "any", "host": "any"}],
    }
    base.update(overrides)
    return {"nebula": base}


class TestDisabledOrIncomplete:
    def test_disabled_returns_empty(self):
        assert nebula.generate({"nebula": {"enabled": False}}) == ""

    def test_enabled_without_credentials_returns_empty(self):
        assert nebula.generate(_nb(cert_pem="", key_pem="", ca_pem="")) == ""

    def test_missing_nebula_key_returns_empty(self):
        assert nebula.generate({}) == ""


class TestBasicShape:
    def test_pki_paths(self):
        out = nebula.generate(_nb())
        assert 'ca: "/etc/nebula/ca.crt"' in out
        assert 'cert: "/etc/nebula/host.crt"' in out
        assert 'key: "/etc/nebula/host.key"' in out

    def test_am_lighthouse_always_false(self):
        assert "am_lighthouse: false" in nebula.generate(_nb())

    def test_listen_port(self):
        out = nebula.generate(_nb(listen_port=5555))
        assert "port: 5555" in out

    def test_dev_is_nebula1(self):
        assert "dev: nebula1" in nebula.generate(_nb())


class TestLighthouseHosts:
    def test_empty_hosts_inline(self):
        out = nebula.generate(_nb(lighthouse_hosts=[]))
        assert "hosts: []" in out

    def test_hosts_listed(self):
        out = nebula.generate(_nb(lighthouse_hosts=["192.168.100.1", "192.168.100.2"]))
        assert '- "192.168.100.1"' in out
        assert '- "192.168.100.2"' in out


class TestStaticHostMap:
    def test_empty_inline(self):
        out = nebula.generate(_nb(static_host_map={}))
        assert "static_host_map: {}" in out

    def test_entries(self):
        out = nebula.generate(_nb(static_host_map={"192.168.100.1": ["lh.example.com:4242"]}))
        assert '"192.168.100.1": ["lh.example.com:4242"]' in out


class TestFirewall:
    def test_empty_outbound_inline(self):
        out = nebula.generate(_nb(firewall_outbound=[]))
        assert "outbound: []" in out

    def test_empty_inbound_inline(self):
        out = nebula.generate(_nb(firewall_inbound=[]))
        assert "inbound: []" in out

    def test_inbound_rule_rendered(self):
        out = nebula.generate(_nb(firewall_inbound=[{"port": "22", "proto": "tcp", "host": "any"}]))
        assert "port: 22" in out
        assert "proto: tcp" in out

    def test_default_outbound_allow_all(self):
        out = nebula.generate(_nb())
        assert "port: any" in out
        assert "proto: any" in out


class TestYamlIsParseable:
    def test_round_trips_via_pyyaml(self):
        yaml = pytest.importorskip("yaml")
        out = nebula.generate(_nb(
            lighthouse_hosts=["192.168.100.1"],
            static_host_map={"192.168.100.1": ["lh.example.com:4242"]},
            firewall_inbound=[{"port": "22", "proto": "tcp", "host": "any"}],
        ))
        parsed = yaml.safe_load(out)
        assert parsed["pki"]["ca"] == "/etc/nebula/ca.crt"
        assert parsed["lighthouse"]["am_lighthouse"] is False
        assert parsed["listen"]["port"] == 4242
        assert parsed["firewall"]["inbound"][0]["port"] == 22
