#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
#
# spud-router — root-owned commit-confirm wrapper for the connectivity-
# watchdog auto-revert.
#
# Installed at /opt/spud-router/spud-commit.sh, owner root:root, mode 0755.
# The non-root spud-router service is granted NOPASSWD sudo on exactly
# "spud-commit.sh arm <N>" and "spud-commit.sh confirm" (see deploy/sudoers)
# — nothing else. This script is the only thing that may run as root on
# the service's behalf for this feature, so it must stay minimal and
# non-writable by spud-router (mirrors run-update.sh's own precedent).
#
# systemd-run detaches the scheduled revert into its own transient
# unit/cgroup so it survives the `systemctl restart spud-router` that a
# reverted apply may itself trigger. --collect auto-removes the transient
# unit once it exits.
set -euo pipefail

case "${1:-}" in
  arm)
    WINDOW="${2:-}"
    if [[ ! "$WINDOW" =~ ^[0-9]+$ ]]; then
        echo "usage: spud-commit.sh arm <seconds>" >&2
        exit 2
    fi
    if (( WINDOW < 10 || WINDOW > 3600 )); then
        echo "window must be between 10 and 3600 seconds" >&2
        exit 2
    fi
    # Re-arming (e.g. a second Apply before the first was confirmed)
    # replaces any previous pending revert rather than stacking two.
    systemctl stop spud-router-revert.timer spud-router-revert.service 2>/dev/null || true
    exec systemd-run --on-active="${WINDOW}s" --unit=spud-router-revert --collect \
        /opt/spud-router/spud-commit.sh revert
    ;;
  confirm)
    systemctl stop spud-router-revert.timer spud-router-revert.service 2>/dev/null || true
    ;;
  revert)
    # Fired by the timer scheduled in `arm` above — already running as
    # root. Delegates the actual restore+reactivate to update.py (system
    # python3, no pip deps), so that logic lives in one place, shared with
    # --tls-restart's sibling detached-restart pattern.
    exec /usr/bin/python3 /opt/spud-router/update.py --revert
    ;;
  *)
    echo "usage: spud-commit.sh {arm <seconds>|confirm|revert}" >&2
    exit 2
    ;;
esac
