#!/bin/bash
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
  *)
    echo "usage: run-update.sh {apply|reboot}" >&2
    exit 2
    ;;
esac
