#!/usr/bin/env bash
#
# ATOMICam installer
# -----------------------------------------------------------------------------
# Installs the ATOMICam multi-camera lab viewer as a set of systemd services on
# a Raspberry Pi (Debian Bookworm / Bullseye). Run as root from the repo root:
#
#     sudo ./install.sh
#
# It is safe to re-run: existing camera configs are kept, and everything else is
# refreshed in place. To remove ATOMICam, use ./uninstall.sh.
# -----------------------------------------------------------------------------
set -euo pipefail

# ── Configuration ────────────────────────────────────────────────────────────
APP_USER="atomicam"
APP_DIR="/opt/ATOMICam"
DATA_DIR="/var/lib/atomicam"
MOTION_CONF_DIR="/etc/motion/cameras"
CONFIG_FILE="$DATA_DIR/cameras.json"
SUDOERS_FILE="/etc/sudoers.d/atomicam"
USB_RESET_HELPER="/usr/local/sbin/atomicam-usb-reset"
NUM_CAMERAS=4
BASE_PORT=8081
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Camera id N defaults to /dev/video(2N): one capture node + one metadata node
# per USB camera. These are just seeds — reassign devices later in the web UI.
default_device_for() { echo "/dev/video$(( $1 * 2 ))"; }

log()  { printf '\033[0;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[0;33m[!]\033[0m %s\n' "$*"; }
die()  { printf '\033[0;31m[x] %s\033[0m\n' "$*" >&2; exit 1; }

# ── Preflight ────────────────────────────────────────────────────────────────
[ "$(id -u)" -eq 0 ] || die "Please run as root:  sudo ./install.sh"
command -v apt-get >/dev/null || die "Debian-based system expected (apt-get not found)."
[ -f "$SCRIPT_DIR/ATOMICam.py" ]            || die "ATOMICam.py not found next to install.sh — run from the repo root."
[ -f "$SCRIPT_DIR/templates/index.html" ]   || die "templates/index.html not found — run from the repo root."

# ── 1. System packages ───────────────────────────────────────────────────────
log "Installing system packages (motion, uhubctl, v4l-utils, Flask)…"
apt-get update
apt-get install -y motion uhubctl v4l-utils python3-flask

# Debian's motion package ships an always-on service that would grab the
# cameras; we run our own per-camera instances instead, so disable it.
systemctl disable --now motion.service 2>/dev/null || true

# ── 2. Service user + group ──────────────────────────────────────────────────
getent group "$APP_USER" >/dev/null || { log "Creating group '$APP_USER'…"; groupadd --system "$APP_USER"; }
if ! id "$APP_USER" >/dev/null 2>&1; then
    log "Creating system user '$APP_USER'…"
    useradd --system --gid "$APP_USER" --no-create-home --shell /usr/sbin/nologin "$APP_USER"
fi
# Needs the video group to read/set v4l2 controls on /dev/video*
usermod -aG video "$APP_USER"

# ── 3. Application files ──────────────────────────────────────────────────────
log "Installing application to $APP_DIR…"
install -d -o "$APP_USER" -g "$APP_USER" "$APP_DIR" "$APP_DIR/templates"
install -o "$APP_USER" -g "$APP_USER" -m 0644 "$SCRIPT_DIR/ATOMICam.py"          "$APP_DIR/ATOMICam.py"
install -o "$APP_USER" -g "$APP_USER" -m 0644 "$SCRIPT_DIR/templates/index.html" "$APP_DIR/templates/index.html"

# ── 4. Writable data directory (holds cameras.json) ──────────────────────────
log "Creating data directory $DATA_DIR…"
install -d -o "$APP_USER" -g "$APP_USER" -m 0755 "$DATA_DIR"

# ── 5. Motion per-camera configs ─────────────────────────────────────────────
log "Writing Motion camera configs to $MOTION_CONF_DIR…"
install -d "$MOTION_CONF_DIR"
for i in $(seq 0 $((NUM_CAMERAS - 1))); do
    conf="$MOTION_CONF_DIR/cam$i.conf"
    port=$((BASE_PORT + i))
    num=$((i + 1))
    dev="$(default_device_for "$i")"
    if [ -f "$conf" ]; then
        warn "Keeping existing $conf (not overwriting)."
        continue
    fi
    cat > "$conf" <<EOF
# ATOMICam — Motion config for Camera $num ($dev)
#
# The ATOMICam web UI manages: videodevice, rotate, width, height.
# Other settings are safe to tune. Directive names can vary between Motion
# versions; if a camera won't stream, check journalctl / the log file below.

############################################################
# Capture source
############################################################
videodevice     $dev
v4l2_palette    8
width           640
height          480
framerate       15
rotate          0
auto_brightness off

############################################################
# Streaming (MJPEG over HTTP)
############################################################
stream_port         $port
stream_quality      75
stream_maxrate      15
stream_localhost    off
stream_auth_method  0

############################################################
# Motion detection — DISABLED (live streaming only)
############################################################
threshold       2147483647
event_gap       9999999
output_pictures off
movie_output    off

############################################################
# Logging
############################################################
log_level   5
log_file    /var/log/motion/cam$i.log

############################################################
# Text overlays
############################################################
text_left       Camera $num
text_right      %Y-%m-%d\n%T

############################################################
# PID file
############################################################
pid_file  /var/run/motion/cam$i.pid
EOF
done

# The configs above write a per-camera log file and PID file, so make sure
# Motion's log and runtime directories exist (the runtime dir lives on tmpfs and
# is recreated each boot via tmpfiles.d).
install -d -m 0755 /var/log/motion
install -d -m 0755 /run/motion
cat > /etc/tmpfiles.d/atomicam-motion.conf <<'EOF'
# Recreate Motion's runtime dir on every boot (holds per-camera PID files)
d /run/motion 0755 root root -
EOF
systemd-tmpfiles --create /etc/tmpfiles.d/atomicam-motion.conf 2>/dev/null || true

# The web app (running as $APP_USER) rewrites these config files when you change
# a device/rotation/resolution, so make the directory and files group-writable.
chown -R root:"$APP_USER" "$MOTION_CONF_DIR"
chmod 0775 "$MOTION_CONF_DIR"
for i in $(seq 0 $((NUM_CAMERAS - 1))); do chmod 0664 "$MOTION_CONF_DIR/cam$i.conf"; done

# ── 6. systemd units ─────────────────────────────────────────────────────────
log "Installing systemd units…"

cat > /etc/systemd/system/ATOMICam-motion@.service <<'EOF'
[Unit]
Description=ATOMICam Motion stream — camera %i
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/motion -c /etc/motion/cameras/cam%i.conf -n
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/ATOMICam.service <<EOF
[Unit]
Description=ATOMICam Web UI (Flask)
After=network.target

[Service]
Type=simple
User=$APP_USER
Group=$APP_USER
SupplementaryGroups=video
WorkingDirectory=$APP_DIR
Environment=ATOMICAM_CONFIG=$CONFIG_FILE
Environment=ATOMICAM_MOTION_CONF_DIR=$MOTION_CONF_DIR
ExecStart=/usr/bin/python3 $APP_DIR/ATOMICam.py
Restart=on-failure
RestartSec=5

# Do NOT add NoNewPrivileges=true — it would block the sudo that the reboot and
# USB power-cycle buttons rely on.

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload

# ── 6b. USB reset helper (root-owned; used by the "Reset Cameras" action) ─────
# Resets all connected USB cameras generically by toggling their kernel USB
# authorization — no special hub hardware needed. Installed root-owned in
# /usr/local/sbin (NOT the app dir) so the service user can't modify a script it
# is allowed to run as root.
log "Installing USB reset helper at $USB_RESET_HELPER…"
cat > "$USB_RESET_HELPER" <<'EOF'
#!/usr/bin/env bash
# atomicam-usb-reset — re-enumerate every connected USB camera by toggling its
# USB device authorization. Generic and hardware-independent. Run as root.
set -u

declare -A usbdevs
for vdev in /sys/class/video4linux/video*; do
    [ -e "$vdev/device" ] || continue
    iface="$(readlink -f "$vdev/device" 2>/dev/null)" || continue
    usbdev="$(dirname "$iface")"
    [ -f "$usbdev/authorized" ] || continue      # keep only real USB devices
    usbdevs["$usbdev"]=1
done

if [ "${#usbdevs[@]}" -eq 0 ]; then
    echo "No USB cameras found to reset." >&2
    exit 0
fi

# Disconnect all, pause, then reconnect all (forces re-enumeration)
for usbdev in "${!usbdevs[@]}"; do echo 0 > "$usbdev/authorized" || true; done
sleep 2
for usbdev in "${!usbdevs[@]}"; do echo 1 > "$usbdev/authorized" || true; done
EOF
chown root:root "$USB_RESET_HELPER"
chmod 0755 "$USB_RESET_HELPER"

# ── 7. Scoped passwordless sudo ──────────────────────────────────────────────
log "Installing scoped sudoers rules…"
REBOOT_BIN="$(command -v reboot   || echo /usr/sbin/reboot)"
UHUBCTL_BIN="$(command -v uhubctl  || echo /usr/sbin/uhubctl)"
SYSTEMCTL_BIN="$(command -v systemctl || echo /usr/bin/systemctl)"

TMP_SUDO="$(mktemp)"
cat > "$TMP_SUDO" <<EOF
# Managed by the ATOMICam installer — passwordless for EXACTLY these commands.
$APP_USER ALL=(root) NOPASSWD: $REBOOT_BIN
$APP_USER ALL=(root) NOPASSWD: $USB_RESET_HELPER
$APP_USER ALL=(root) NOPASSWD: $UHUBCTL_BIN -l 1-1 -a cycle -d 5
$APP_USER ALL=(root) NOPASSWD: $SYSTEMCTL_BIN restart ATOMICam-motion@[0-3].service
$APP_USER ALL=(root) NOPASSWD: $SYSTEMCTL_BIN stop ATOMICam-motion@[0-3].service
EOF
visudo -cf "$TMP_SUDO" || { rm -f "$TMP_SUDO"; die "Generated sudoers failed validation — nothing installed."; }
install -o root -g root -m 0440 "$TMP_SUDO" "$SUDOERS_FILE"
rm -f "$TMP_SUDO"

# ── 8. Enable + start services ───────────────────────────────────────────────
log "Enabling and starting services…"
for i in $(seq 0 $((NUM_CAMERAS - 1))); do
    systemctl enable --now "ATOMICam-motion@$i.service"
done
systemctl enable --now ATOMICam.service

# ── Summary ──────────────────────────────────────────────────────────────────
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo
log "ATOMICam installed."
cat <<EOF

  Open the interface:   http://${IP:-<pi-ip>}:5000

  Configure cameras:    Admin tab in the web UI (Detect → assign → label)
  Config / data file:   $CONFIG_FILE
  Motion configs:       $MOTION_CONF_DIR/cam0.conf … cam$((NUM_CAMERAS - 1)).conf
  App logs:             journalctl -u ATOMICam -f
  Camera logs:          journalctl -u 'ATOMICam-motion@*' -f

  If a camera doesn't stream, check its log and verify the Motion config
  directives against a known-good config from your working setup.

EOF
