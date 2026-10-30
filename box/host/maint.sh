#!/bin/bash
# Planned, drained maintenance of the render box (E1, 30 Oct 2026).
#
#   flock -w 3600 /var/lock/valmera-maint /opt/valmera/box/host/maint.sh reboot
#       drain admission (new calls are refused with proof nothing ran, so the
#       dispatcher waits: in the claim, then in the Postgres queue), wait
#       until no call runs, mark the maintenance window in app_kv box_health
#       (the P0 watch stays quiet until it ends), then reboot. Aborts and
#       un-drains when the box does not go idle within MAINT_IDLE_WAIT_S.
#   maint.sh resume   (valmera-resume.service, every boot) un-drain after a
#       planned reboot and close the window.
#   maint.sh status   one line: draining, busy, queued, reboot-required.
#
# Nothing here reboots by itself: unattended upgrades never reboot or
# restart services (52valmera-unattended, needrestart list-only); a reboot
# is always this script, run on purpose.
set -euo pipefail
DATA=/var/lib/valmera
ADMIN=http://127.0.0.1:8090
LOG=/var/log/valmera/maint.log
IDLE_WAIT=${MAINT_IDLE_WAIT_S:-3600}
WINDOW=${MAINT_WINDOW_S:-900}

log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$LOG"; }
token() { cat /etc/valmera/admin.token; }
adm() { curl -sS -m 10 -H "X-Admin-Token: $(token)" -H 'Content-Type: application/json' "$@"; }
field() { python3 -c "import json,sys; d=json.load(sys.stdin); print($1)"; }
drain() { adm -X POST -d "{\"on\": $1}" "$ADMIN/admin/drain" >/dev/null; }

case "${1:-status}" in
  status)
    adm "$ADMIN/admin/state" | field '"draining=%s busy=%s queued=%s reboot_required=%s" % (d["draining"], d["slots"]["busy"], d["queue"]["depth"], d.get("reboot_required"))'
    ;;
  reboot)
    log "maint: draining admission for a planned reboot"
    drain true
    # Never leave the box drained by accident: any error from here on
    # un-drains before the script exits.
    trap 'drain false || true; log "maint: error, un-drained"' ERR
    deadline=$(( $(date +%s) + IDLE_WAIT ))
    while true; do
      busy=$(adm "$ADMIN/admin/state" | field 'd["slots"]["busy"]') \
        || busy=unknown
      [[ "$busy" == "0" ]] && break
      if (( $(date +%s) > deadline )); then
        drain false
        log "maint: ABORTED, $busy call(s) still running after ${IDLE_WAIT}s; un-drained"
        exit 1
      fi
      sleep 10
    done
    touch "$DATA/undrain-on-boot"
    echo $(( $(date +%s) + WINDOW )) > "$DATA/maint-until"
    python3 /opt/valmera/box/health_report.py || true
    log "maint: idle and drained; rebooting (window ${WINDOW}s)"
    trap - ERR
    sync
    systemctl reboot
    ;;
  resume)
    for _ in $(seq 1 60); do
      curl -s -m 3 "$ADMIN/ping" >/dev/null 2>&1 && break
      sleep 2
    done
    if [[ -f "$DATA/undrain-on-boot" ]]; then
      drain false
      rm -f "$DATA/undrain-on-boot"
      log "maint: boot after a planned reboot; admission un-drained"
    fi
    rm -f "$DATA/maint-until"
    python3 /opt/valmera/box/health_report.py || true
    ;;
  *)
    echo "usage: maint.sh status|reboot|resume" >&2
    exit 2
    ;;
esac
