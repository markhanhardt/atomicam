#!/usr/bin/env bash
#
# ATOMICam uninstaller
# -----------------------------------------------------------------------------
# Removes the ATOMICam services, application, and sudoers rules. By default it
# KEEPS your camera configuration (cameras.json and the Motion configs) so a
# reinstall picks up where you left off. Pass --purge to remove those too.
#
#     sudo ./uninstall.sh            # remove app, keep camera config
#     sudo ./uninstall.sh --purge    # remove everything, including config
# -----------------------------------------------------------------------------
set -uo pipefail

APP_USER="atomicam"
APP_DIR="/opt/ATOMICam"
DATA_DIR="/var/lib/atomicam"
MOTION_CONF_DIR="/etc/motion/cameras"
SUDOERS_FILE="/etc/sudoers.d/atomicam"
NUM_CAMERAS=4
PURGE=0
[ "${1:-}" = "--purge" ] && PURGE=1

log() { printf '\033[0;36m==>\033[0m %s\n' "$*"; }

[ "$(id -u)" -eq 0 ] || { echo "Please run as root:  sudo ./uninstall.sh"; exit 1; }

log "Stopping and disabling services…"
systemctl disable --now ATOMICam.service 2>/dev/null || true
for i in $(seq 0 $((NUM_CAMERAS - 1))); do
    systemctl disable --now "ATOMICam-motion@$i.service" 2>/dev/null || true
done

log "Removing systemd units and sudoers…"
rm -f /etc/systemd/system/ATOMICam.service /etc/systemd/system/ATOMICam-motion@.service
rm -f "$SUDOERS_FILE"
rm -f /usr/local/sbin/atomicam-usb-reset
rm -f /etc/tmpfiles.d/atomicam-motion.conf
systemctl daemon-reload

log "Removing application files…"
rm -rf "$APP_DIR"

if [ "$PURGE" -eq 1 ]; then
    log "Purging camera configuration and data…"
    rm -rf "$DATA_DIR"
    rm -f "$MOTION_CONF_DIR"/cam*.conf
    if id "$APP_USER" >/dev/null 2>&1; then
        log "Removing user '$APP_USER'…"
        userdel "$APP_USER" 2>/dev/null || true
    fi
    if getent group "$APP_USER" >/dev/null; then
        groupdel "$APP_USER" 2>/dev/null || true
    fi
else
    log "Kept camera config: $DATA_DIR and $MOTION_CONF_DIR/cam*.conf"
    log "User '$APP_USER' left in place. Use --purge to remove everything."
fi

log "Done."
