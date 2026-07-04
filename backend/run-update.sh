#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
# spud-router — root-owned update/reboot wrapper.
#
# Installed at /opt/spud-router/run-update.sh, owner root:root, mode 0755.
# The non-root spud-router service is granted NOPASSWD sudo on exactly
# "run-update.sh apply" and "run-update.sh reboot" (see /etc/sudoers.d/
# spud-router) — nothing else. This script is the only thing that may run
# as root on the service's behalf, so it must stay minimal and non-writable
# by spud-router.
#
# systemd-run detaches the actual work into its own transient unit/cgroup so
# it survives `systemctl restart spud-router` (the updater's own restart
# step would otherwise kill its own parent process and truncate progress).
# --collect auto-removes the transient unit once it exits.
set -euo pipefail
mkdir -p /run/spud-router

case "${1:-}" in
  apply)
    exec systemd-run --unit=spud-router-update --collect \
        /usr/bin/python3 /opt/spud-router/update.py --apply
    ;;
  reboot)
    # The 2s delay lets the HTTP response for POST /api/system/reboot flush
    # back to the client before the box actually goes down.
    exec systemd-run --on-active=2s --unit=spud-router-reboot --collect \
        /usr/bin/systemctl reboot
    ;;
  tls-restart)
    # Detached so it survives the `systemctl restart spud-router` it
    # performs — same reasoning as `apply` above. Also lets the HTTP
    # response for the triggering POST flush back before the restart.
    exec systemd-run --unit=spud-router-tls-restart --collect \
        /usr/bin/python3 /opt/spud-router/update.py --tls-restart
    ;;
  *)
    echo "usage: run-update.sh {apply|reboot|tls-restart}" >&2
    exit 2
    ;;
esac
