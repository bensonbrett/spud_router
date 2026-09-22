#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
set -euo pipefail

# This root-owned helper consumes firewall *data*, never an executable script.
rules=/etc/spud-router/iptables.rules
[[ -f "$rules" && ! -L "$rules" ]] || { echo "missing firewall rules" >&2; exit 1; }
/usr/sbin/iptables-restore < "$rules"
mkdir -p /etc/iptables
/usr/sbin/iptables-save > /etc/iptables/rules.v4
