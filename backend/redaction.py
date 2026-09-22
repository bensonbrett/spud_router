# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""Read-safe representations of persisted router state."""
from copy import deepcopy

MASK = "********"
_SECRETS = {
    "wireguard": ("private_key",),
    "nebula": ("key_pem", "cert_pem", "ca_pem"),
    "snmp": ("community_ro", "community_rw"),
}


def public_state(state: dict) -> dict:
    result = deepcopy(state)
    for section, fields in _SECRETS.items():
        value = result.get(section, {})
        if isinstance(value, dict):
            for field in fields:
                if value.get(field):
                    value[field] = MASK
    for ssid in result.get("wireless", {}).get("ssids", []):
        if isinstance(ssid, dict) and ssid.get("password"):
            ssid["password"] = MASK
    return result


def redact_text(text: str, state: dict) -> str:
    for section, fields in _SECRETS.items():
        value = state.get(section, {})
        if isinstance(value, dict):
            for field in fields:
                if value.get(field):
                    text = text.replace(value[field], MASK)
    return text
