#!/usr/bin/env python3

import subprocess
import re
import os
import json
import time
import urllib.request
import urllib.error
from flask import Flask, render_template, jsonify, request

app = Flask(__name__)

VERSION = "1.2.1"

GITHUB_REPO = "markhanhardt/atomicam"

# ── Paths & constants (env-overridable so the app runs in dev and production) ──
# In production the systemd unit sets ATOMICAM_CONFIG to a writable location such
# as /var/lib/atomicam/cameras.json; in dev it defaults to sitting next to this
# file. MOTION_CONF_DIR is where the installer writes the per-camera Motion
# configs that this app edits when the user changes a device or rotation.
CONFIG_FILE = os.environ.get(
    "ATOMICAM_CONFIG",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "cameras.json"),
)
MOTION_CONF_DIR  = os.environ.get("ATOMICAM_MOTION_CONF_DIR", "/etc/motion/cameras")
MOTION_UNIT      = "ATOMICam-motion@{id}.service"
# Root-owned helper (installed to /usr/local/sbin, NOT writable by the app user)
# that resets USB cameras generically. Kept outside the app dir on purpose: the
# app runs it via sudo, so it must not be editable by the service user.
USB_RESET_HELPER = os.environ.get("ATOMICAM_USB_RESET", "/usr/local/sbin/atomicam-usb-reset")
NUM_CAMERAS      = 4
BASE_STREAM_PORT = 8081          # camera id N streams on BASE_STREAM_PORT + N
VALID_ROTATIONS  = (0, 90, 180, 270)
# 4:3 resolutions only, to keep the display layout and reticle geometry valid.
VALID_RESOLUTIONS = [(320, 240), (640, 480), (800, 600), (1024, 768), (1280, 960)]
DEFAULT_WIDTH, DEFAULT_HEIGHT = 640, 480
VALID_RETICLE_TYPES = ("off", "a", "b", "c")
DEFAULT_RETICLE_OPACITY = 0.5


def save_cameras(cams):
    """Persist camera definitions (without the derived port) atomically."""
    slim = []
    for c in cams:
        entry = {
            "id": c["id"], "label": c["label"], "device": c["device"],
            "rotate": c.get("rotate", 0),
            "width": c.get("width", DEFAULT_WIDTH), "height": c.get("height", DEFAULT_HEIGHT),
        }
        if isinstance(c.get("reticle"), dict):   # per-camera overlay, if set
            entry["reticle"] = c["reticle"]
        slim.append(entry)
    tmp = CONFIG_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(slim, f, indent=2)
    os.replace(tmp, CONFIG_FILE)


def _is_capture_device(dev):
    """True only if THIS node's own Device Caps include Video Capture.

    Modern UVC cameras expose two /dev/video nodes: the real capture node and a
    companion metadata node. Both share the driver-wide 'Capabilities' block, so
    a naive search for 'Video Capture' matches both. Only the capture node lists
    'Video Capture' in its per-node 'Device Caps'; checking that block excludes
    the metadata node (which otherwise shows up and displays a grey screen)."""
    try:
        out = subprocess.check_output(
            ["v4l2-ctl", "-d", dev, "--all"],
            stderr=subprocess.DEVNULL, timeout=3
        ).decode()
    except Exception:
        return False
    # Isolate the per-node "Device Caps" block (its indented capability lines).
    m = re.search(r"Device Caps\s*:.*?\n((?:[ \t]+\S.*\n)+)", out)
    caps_block = m.group(1) if m else out   # fall back on drivers that omit it
    return "Video Capture" in caps_block


def _is_usb_video(dev):
    """True only for USB-connected video nodes. Excludes the Pi's on-SoC video
    blocks (bcm2835-codec, bcm2835-isp, rpi-hevc-dec, ...) which are
    memory-to-memory devices that also report 'Video Capture' but are not
    cameras."""
    node = os.path.basename(dev)
    try:
        real = os.path.realpath(f"/sys/class/video4linux/{node}/device")
    except Exception:
        return False
    return "/usb" in real


def _stable_path(dev):
    """Return a stable udev path for a /dev/videoN node so a camera's identity
    survives re-enumeration (e.g. after a USB power-cycle or reset). Uses the
    physical USB port path (/dev/v4l/by-path), which is stable for a fixed rig
    and distinguishes even identical cameras. Falls back to the raw node."""
    try:
        target = os.path.realpath(dev)
        by_path = "/dev/v4l/by-path"
        for name in sorted(os.listdir(by_path)):
            link = os.path.join(by_path, name)
            if os.path.realpath(link) == target:
                return link
    except OSError:
        pass
    return dev


def _port_hint(path):
    """Short USB-port label (e.g. '1.2') pulled from a by-path device, so
    identical cameras are distinguishable in the assignment menu."""
    m = re.search(r"usb-\d+:([\d.]+):", path or "")
    return m.group(1) if m else None


def _parse_v4l2_devices(listing):
    """Parse `v4l2-ctl --list-devices` output into real USB cameras.

    Each USB camera exposes several /dev/videoN nodes (a capture node plus
    metadata nodes); we keep only USB-connected nodes that actually support
    capture, so the user picks from real cameras rather than phantom devices or
    the Pi's on-SoC codec/ISP nodes. Devices are identified by a stable udev
    port path rather than the volatile /dev/videoN.
    """
    devices = []
    current_name = None
    for line in listing.splitlines():
        if not line.strip():
            continue
        if not line.startswith(("\t", " ")):
            current_name = line.rstrip(":").strip()
        else:
            path = line.strip()
            if (re.fullmatch(r"/dev/video\d+", path)
                    and _is_usb_video(path)
                    and _is_capture_device(path)):
                stable = _stable_path(path)
                hint = _port_hint(stable)
                label = current_name or path
                # Drop v4l2-ctl's verbose "(usb-...)" bus suffix; we add a short one.
                label = re.sub(r"\s*\(usb-[^)]*\)\s*$", "", label)
                if hint:
                    label = f"{label} (USB {hint})"
                devices.append({"name": label, "path": stable})
    return devices


def _detect_capture_paths():
    """Device paths of connected USB capture cameras (real nodes only)."""
    try:
        out = subprocess.check_output(
            ["v4l2-ctl", "--list-devices"],
            stderr=subprocess.DEVNULL, timeout=5
        ).decode()
        return [d["path"] for d in _parse_v4l2_devices(out)]
    except Exception:
        return []


def _seed_cameras():
    """Build the initial camera list: assign the cameras actually attached to the
    first slots (in order) and leave the remaining slots as 'No camera', so only
    connected cameras appear on first run."""
    detected = _detect_capture_paths()
    return [
        {
            "id": i, "label": f"Camera {i + 1}",
            "device": detected[i] if i < len(detected) else "",
            "rotate": 0, "width": DEFAULT_WIDTH, "height": DEFAULT_HEIGHT,
        }
        for i in range(NUM_CAMERAS)
    ]


def load_cameras():
    """Load camera definitions from CONFIG_FILE, seeding defaults if it's
    missing or unreadable. The stream port is always derived from the id so it
    can't drift out of sync with the Motion configs. Sets the module-level
    FRESH_SEED flag when it had to seed, so first boot can push the detected
    devices into Motion."""
    global FRESH_SEED, MIGRATED
    try:
        with open(CONFIG_FILE) as f:
            cams = json.load(f)
        assert isinstance(cams, list) and cams
        for c in cams:
            c["id"]     = int(c["id"])
            c["label"]  = str(c["label"])
            c["device"] = str(c["device"])
            # Migrate legacy configs that reference a raw /dev/videoN (which can
            # shuffle on re-enumeration) to a stable udev port path.
            if re.fullmatch(r"/dev/video\d+", c["device"]):
                stable = _stable_path(c["device"])
                if stable != c["device"]:
                    c["device"] = stable
                    MIGRATED = True
            c["rotate"] = int(c.get("rotate", 0))
            c["width"]  = int(c.get("width", DEFAULT_WIDTH))
            c["height"] = int(c.get("height", DEFAULT_HEIGHT))
            if not isinstance(c.get("reticle"), dict):
                c.pop("reticle", None)
        if MIGRATED:
            try:
                save_cameras(cams)   # persist stable paths so we migrate only once
            except Exception:
                pass
    except Exception:
        cams = _seed_cameras()
        FRESH_SEED = True
        try:
            save_cameras(cams)
        except Exception:
            pass  # read-only dev environment — fall back to in-memory defaults
    for c in cams:
        c["port"] = BASE_STREAM_PORT + c["id"]
    return cams


FRESH_SEED = False
MIGRATED = False


CAMERAS = load_cameras()

# v4l2 control names mapped to friendly names
V4L2_CONTROLS = {
    "brightness":   {"label": "Brightness",  "min": -255,    "max": 255, "default": 0},
    "contrast":     {"label": "Contrast",    "min": 0,    "max": 30, "default": 16},
    "saturation":   {"label": "Saturation",  "min": 0,    "max": 127, "default": 36},
    "hue":          {"label": "Hue",         "min": -16000, "max": 16000, "default": 0},
    "sharpness":    {"label": "Sharpness",   "min": 0,    "max": 15, "default": 4},
#    "gain":         {"label": "Gain",        "min": 0,    "max": 255, "default": 0},
    "gamma":         {"label": "Gamma",        "min": 20,    "max": 250, "default": 100},
    "backlight_compensation": {"label": "Backlight Comp", "min": 0, "max": 1, "default": 1},
}

# ── Routes ────────────────────────────────────────────────────────────────────

def _config_rev():
    """A cheap 'has the config changed?' marker: the config file's mtime. Viewers
    poll this via /api/health and re-sync when it changes, so a reticle drawn on
    one computer appears on the others without a manual refresh."""
    try:
        return os.path.getmtime(CONFIG_FILE)
    except OSError:
        return 0.0


@app.route("/")
def index():
    # `cameras` is every slot (the config panel needs them all); `active_cameras`
    # is only the slots with a device assigned, which is what actually gets shown.
    active = [c for c in CAMERAS if c.get("device")]
    default_cam_id = active[0]["id"] if active else 0
    return render_template("index.html", cameras=CAMERAS,
                           active_cameras=active, default_cam_id=default_cam_id,
                           version=VERSION, config_rev=_config_rev(),
                           github_repo=GITHUB_REPO)

@app.route("/api/health")
def api_health():
    """Tiny liveness endpoint the page polls to detect reboots/outages. Also
    carries the config revision so viewers can live-sync each other's changes."""
    return jsonify({"ok": True, "rev": _config_rev()})


def _parse_version(v):
    """Turn a version string like 'v1.2.0' or '1.2' into a comparable tuple."""
    return tuple(int(n) for n in re.findall(r"\d+", v or "")[:3])


@app.route("/api/update-check")
def api_update_check():
    """Compare the running version against the latest GitHub release. Needs
    internet; fails quietly when offline so it never disturbs the otherwise
    offline-only app. Statuses: ok / no-releases / error / offline."""
    info = {"current": VERSION, "latest": None, "update_available": False,
            "url": None, "status": "ok"}
    api = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
    try:
        req = urllib.request.Request(api, headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"ATOMICam/{VERSION}",
        })
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.load(resp)
        latest = (data.get("tag_name") or "").strip()
        info["latest"] = latest
        info["url"] = data.get("html_url") or f"https://github.com/{GITHUB_REPO}/releases"
        info["update_available"] = bool(latest) and _parse_version(latest) > _parse_version(VERSION)
    except urllib.error.HTTPError as e:
        info["status"] = "no-releases" if e.code == 404 else "error"
    except Exception:
        info["status"] = "offline"   # no internet / DNS failure / timeout
    return jsonify(info)

@app.route("/api/controls/<int:cam_id>")
def api_get_controls(cam_id):
    """Read current v4l2 control values for a camera."""
    cam = next((c for c in CAMERAS if c["id"] == cam_id), None)
    if not cam:
        return jsonify({"error": "Camera not found"}), 404

    result = {}
    for ctrl_name, meta in V4L2_CONTROLS.items():
        try:
            out = subprocess.check_output(
                ["v4l2-ctl", "-d", cam["device"], "--get-ctrl", ctrl_name],
                stderr=subprocess.DEVNULL,
                timeout=2
            ).decode().strip()
            # Output format: "brightness: 128"
            match = re.search(r":\s*(-?\d+)", out)
            value = int(match.group(1)) if match else meta["default"]
        except Exception:
            value = meta["default"]
        result[ctrl_name] = {**meta, "value": value}
    return jsonify(result)

@app.route("/api/controls/<int:cam_id>", methods=["POST"])
def api_set_control(cam_id):
    """Apply a v4l2 control value to a camera."""
    cam = next((c for c in CAMERAS if c["id"] == cam_id), None)
    if not cam:
        return jsonify({"error": "Camera not found"}), 404

    data = request.get_json()
    ctrl_name = data.get("control")
    value = data.get("value")

    if ctrl_name not in V4L2_CONTROLS:
        return jsonify({"error": "Unknown control"}), 400

    try:
        subprocess.run(
            ["v4l2-ctl", "-d", cam["device"], "--set-ctrl", f"{ctrl_name}={value}"],
            check=True, timeout=2, capture_output=True
        )
        return jsonify({"ok": True, "control": ctrl_name, "value": value})
    except subprocess.CalledProcessError as e:
        return jsonify({"error": e.stderr.decode().strip()}), 500

@app.route("/api/controls/<int:cam_id>/reset", methods=["POST"])
def api_reset_controls(cam_id):
    """Reset all controls to defaults."""
    cam = next((c for c in CAMERAS if c["id"] == cam_id), None)
    if not cam:
        return jsonify({"error": "Camera not found"}), 404

    errors = []
    for ctrl_name, meta in V4L2_CONTROLS.items():
        try:
            subprocess.run(
                ["v4l2-ctl", "-d", cam["device"], "--set-ctrl",
                 f"{ctrl_name}={meta['default']}"],
                timeout=2, capture_output=True
            )
        except Exception as e:
            errors.append(str(e))

    return jsonify({"ok": True, "errors": errors})

# ── Camera detection & configuration ──────────────────────────────────────────

def _supported_resolutions(device):
    """Return the resolutions from VALID_RESOLUTIONS that `device` actually
    supports, by probing `v4l2-ctl --list-formats-ext`. Falls back to the full
    list if the probe fails, so the UI still offers sensible options."""
    try:
        out = subprocess.check_output(
            ["v4l2-ctl", "-d", device, "--list-formats-ext"],
            stderr=subprocess.DEVNULL, timeout=5
        ).decode()
    except Exception:
        return [list(r) for r in VALID_RESOLUTIONS]
    found = {(int(w), int(h)) for w, h in re.findall(r"(\d+)x(\d+)", out)}
    supported = [list(r) for r in VALID_RESOLUTIONS if r in found]
    return supported or [list(r) for r in VALID_RESOLUTIONS]


def _set_conf_value(path, key, value):
    """Replace (or append) a `key value` line in a Motion config file, atomically."""
    with open(path) as f:
        lines = f.readlines()
    pattern = re.compile(rf"^\s*{re.escape(key)}\s+.*$")
    for i, line in enumerate(lines):
        if pattern.match(line):
            lines[i] = f"{key} {value}\n"
            break
    else:
        lines.append(f"{key} {value}\n")
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.writelines(lines)
    os.replace(tmp, path)


def _apply_to_motion(cams):
    """Write device/rotation into each Motion config and restart its instance.

    Returns a per-camera status map. A missing config file or a failed restart
    is reported rather than raised, so a partially-provisioned system (e.g.
    before the installer has run) degrades gracefully instead of erroring out.
    """
    results = {}
    for c in cams:
        unit = MOTION_UNIT.format(id=c["id"])

        # No device assigned → stop this instance so it releases any device and
        # doesn't restart-loop on an empty videodevice.
        if not c.get("device"):
            try:
                subprocess.run(
                    ["sudo", "-n", "systemctl", "stop", unit],
                    check=True, timeout=15, capture_output=True
                )
                results[c["id"]] = "ok"
            except subprocess.CalledProcessError as e:
                results[c["id"]] = f"stop failed: {e.stderr.decode().strip()}"
            except Exception as e:
                results[c["id"]] = f"stop error: {e}"
            continue

        conf = os.path.join(MOTION_CONF_DIR, f"cam{c['id']}.conf")
        if not os.path.exists(conf):
            results[c["id"]] = "config file not found"
            continue
        try:
            _set_conf_value(conf, "videodevice", c["device"])
            _set_conf_value(conf, "rotate", str(c.get("rotate", 0)))
            _set_conf_value(conf, "width", str(c.get("width", DEFAULT_WIDTH)))
            _set_conf_value(conf, "height", str(c.get("height", DEFAULT_HEIGHT)))
        except Exception as e:
            results[c["id"]] = f"config write failed: {e}"
            continue
        try:
            subprocess.run(
                ["sudo", "-n", "systemctl", "restart", unit],
                check=True, timeout=15, capture_output=True
            )
            results[c["id"]] = "ok"
        except subprocess.CalledProcessError as e:
            results[c["id"]] = f"restart failed: {e.stderr.decode().strip()}"
        except Exception as e:
            results[c["id"]] = f"restart error: {e}"
    return results


@app.route("/api/cameras/detect")
def api_detect_cameras():
    """List USB video-capture devices currently connected."""
    try:
        out = subprocess.check_output(
            ["v4l2-ctl", "--list-devices"],
            stderr=subprocess.DEVNULL, timeout=5
        ).decode()
    except Exception as e:
        return jsonify({"error": str(e), "devices": []}), 500
    return jsonify({"devices": _parse_v4l2_devices(out)})


@app.route("/api/cameras/config")
def api_get_camera_config():
    """Return the current camera definitions and the allowed rotation values."""
    return jsonify({"cameras": CAMERAS, "rotations": list(VALID_ROTATIONS)})


@app.route("/api/cameras/config", methods=["POST"])
def api_set_camera_config():
    """Validate and save camera label/device assignments, then push to Motion.

    Rotation and resolution are set per camera in the Dedicated view, so they
    are preserved here from the existing config rather than taken from the
    request — this panel only changes which device (and label) each slot uses.
    """
    global CAMERAS
    data = request.get_json(silent=True) or {}
    incoming = data.get("cameras")
    if not isinstance(incoming, list):
        return jsonify({"error": "Expected a 'cameras' list"}), 400

    existing_by_id = {c["id"]: c for c in CAMERAS}
    new_cams, seen_ids = [], set()
    for c in incoming:
        try:
            cid = int(c["id"])
        except (KeyError, ValueError, TypeError):
            return jsonify({"error": "Each camera needs an integer id"}), 400
        if cid in seen_ids or not (0 <= cid < NUM_CAMERAS):
            return jsonify({"error": f"Invalid or duplicate id: {cid}"}), 400
        seen_ids.add(cid)

        device = str(c.get("device", "")).strip()
        # An empty device means "No camera" for this slot; otherwise it must be a
        # /dev/videoN node or a stable /dev/v4l/by-path (or by-id) device path.
        if device and not re.fullmatch(
                r"/dev/video\d+|/dev/v4l/by-(?:path|id)/[\w.:+-]+", device):
            return jsonify({"error": f"Invalid device path: {device!r}"}), 400

        label = str(c.get("label", "")).strip() or f"Camera {cid + 1}"
        prev = existing_by_id.get(cid, {})
        entry = {
            "id": cid, "label": label, "device": device,
            "rotate": prev.get("rotate", 0),
            "width":  prev.get("width", DEFAULT_WIDTH),
            "height": prev.get("height", DEFAULT_HEIGHT),
        }
        if isinstance(prev.get("reticle"), dict):
            entry["reticle"] = prev["reticle"]
        new_cams.append(entry)

    new_cams.sort(key=lambda c: c["id"])
    try:
        save_cameras(new_cams)
    except Exception as e:
        return jsonify({"error": f"Could not save config: {e}"}), 500

    CAMERAS = load_cameras()
    motion_status = _apply_to_motion(CAMERAS)
    return jsonify({"ok": True, "cameras": CAMERAS, "motion": motion_status})


@app.route("/api/cameras/<int:cam_id>/rotate", methods=["POST"])
def api_set_rotation(cam_id):
    """Set rotation for a single camera and apply it to just that Motion instance."""
    global CAMERAS
    if not any(c["id"] == cam_id for c in CAMERAS):
        return jsonify({"error": "Camera not found"}), 404

    data = request.get_json(silent=True) or {}
    try:
        rotate = int(data.get("rotate"))
    except (ValueError, TypeError):
        return jsonify({"error": "rotate must be a number"}), 400
    if rotate not in VALID_ROTATIONS:
        return jsonify({"error": f"Invalid rotation: {rotate}"}), 400

    updated = [dict(c) for c in CAMERAS]
    for c in updated:
        if c["id"] == cam_id:
            c["rotate"] = rotate
    try:
        save_cameras(updated)
    except Exception as e:
        return jsonify({"error": f"Could not save config: {e}"}), 500

    CAMERAS = load_cameras()
    status = _apply_to_motion([c for c in CAMERAS if c["id"] == cam_id])
    return jsonify({"ok": True, "rotate": rotate, "motion": status.get(cam_id)})


@app.route("/api/cameras/<int:cam_id>/resolutions")
def api_camera_resolutions(cam_id):
    """Return the resolutions the given camera's device actually supports."""
    cam = next((c for c in CAMERAS if c["id"] == cam_id), None)
    if not cam:
        return jsonify({"error": "Camera not found"}), 404
    if not cam.get("device"):
        return jsonify({"resolutions": [], "current": [cam["width"], cam["height"]]})
    res = _supported_resolutions(cam["device"])
    cur = [cam["width"], cam["height"]]
    if cur not in res:              # always let the UI show the current value
        res.append(cur)
    return jsonify({"resolutions": res, "current": cur})


@app.route("/api/cameras/<int:cam_id>/resolution", methods=["POST"])
def api_set_resolution(cam_id):
    """Set resolution for a single camera and apply it to just that instance."""
    global CAMERAS
    if not any(c["id"] == cam_id for c in CAMERAS):
        return jsonify({"error": "Camera not found"}), 404

    data = request.get_json(silent=True) or {}
    try:
        width, height = int(data.get("width")), int(data.get("height"))
    except (ValueError, TypeError):
        return jsonify({"error": "width and height must be numbers"}), 400
    if (width, height) not in VALID_RESOLUTIONS:
        return jsonify({"error": f"Unsupported resolution: {width}x{height}"}), 400

    updated = [dict(c) for c in CAMERAS]
    for c in updated:
        if c["id"] == cam_id:
            c["width"], c["height"] = width, height
    try:
        save_cameras(updated)
    except Exception as e:
        return jsonify({"error": f"Could not save config: {e}"}), 500

    CAMERAS = load_cameras()
    status = _apply_to_motion([c for c in CAMERAS if c["id"] == cam_id])
    return jsonify({"ok": True, "width": width, "height": height, "motion": status.get(cam_id)})


@app.route("/api/cameras/<int:cam_id>/reticle", methods=["POST"])
def api_set_reticle(cam_id):
    """Persist the measurement reticle (style, colour, size, position) for one
    camera. This is a browser overlay only, so nothing is sent to Motion."""
    global CAMERAS
    if not any(c["id"] == cam_id for c in CAMERAS):
        return jsonify({"error": "Camera not found"}), 404

    data = request.get_json(silent=True) or {}
    rtype = str(data.get("type", "off"))
    if rtype not in VALID_RETICLE_TYPES:
        return jsonify({"error": "Invalid reticle type"}), 400
    color = str(data.get("color", "#00e5ff"))
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        return jsonify({"error": "Invalid colour"}), 400
    try:
        r = float(data.get("r", 10))
        x = float(data.get("x", 40))
        y = float(data.get("y", 30))
        opacity = float(data.get("opacity", DEFAULT_RETICLE_OPACITY))
    except (ValueError, TypeError):
        return jsonify({"error": "r/x/y/opacity must be numbers"}), 400

    reticle = {
        "type": rtype, "color": color,
        "r": max(2, min(28, r)), "x": max(0, min(80, x)), "y": max(0, min(60, y)),
        "opacity": max(0.1, min(1.0, opacity)),
    }
    updated = [dict(c) for c in CAMERAS]
    for c in updated:
        if c["id"] == cam_id:
            c["reticle"] = reticle
    try:
        save_cameras(updated)
    except Exception as e:
        return jsonify({"error": f"Could not save config: {e}"}), 500

    CAMERAS = load_cameras()
    return jsonify({"ok": True, "reticle": reticle, "rev": _config_rev()})


# ── Admin actions ─────────────────────────────────────────────────────────────
# These two endpoints run privileged commands through a scoped, passwordless
# sudoers rule (installed by the ATOMICam installer) that permits EXACTLY these
# two invocations for the service user — nothing else. `sudo -n` never prompts:
# if the rule is missing it fails fast instead of hanging on a password prompt
# that has no terminal to answer it. Arguments are fixed lists (no shell=True),
# so there is no command-injection surface.

@app.route("/api/admin/reset-cameras", methods=["POST"])
def api_admin_reset_cameras():
    """Generic, hardware-independent camera recovery: re-enumerate every USB
    camera via the kernel (authorized toggle, done by a root helper), then
    restart Motion so each camera cleanly re-acquires its device. Try this first;
    the hard power-cycle below is the forceful fallback where hardware allows."""
    try:
        subprocess.run(
            ["sudo", "-n", USB_RESET_HELPER],
            check=True, timeout=30, capture_output=True
        )
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Camera reset timed out"}), 500
    except subprocess.CalledProcessError as e:
        return jsonify({"error": e.stderr.decode().strip() or "camera reset failed"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    time.sleep(2)
    motion_status = _apply_to_motion(CAMERAS)
    return jsonify({"ok": True, "motion": motion_status})


@app.route("/api/admin/usb-powercycle", methods=["POST"])
def api_admin_usb_powercycle():
    """Cut and restore power to USB bus 1-1 for 5 s to recover a stuck camera,
    then restart the Motion instances so every camera cleanly re-acquires its
    device. This is required for rotated cameras in particular: Motion's rotation
    buffers don't survive a device drop, so a rotated instance stays wedged after
    a bare power-cycle until it is restarted.

    Note: this needs a hub whose controller supports power switching (uhubctl,
    location 1-1). The software 'reset-cameras' route above works on any
    hardware and is the recommended first step."""
    try:
        subprocess.run(
            ["sudo", "-n", "uhubctl", "-l", "1-1", "-a", "cycle", "-d", "5"],
            check=True, timeout=15, capture_output=True
        )
    except subprocess.TimeoutExpired:
        return jsonify({"error": "USB power-cycle timed out"}), 500
    except subprocess.CalledProcessError as e:
        return jsonify({"error": e.stderr.decode().strip() or "uhubctl failed"}), 500

    # Give the bus a moment to re-enumerate, then re-apply the config so each
    # Motion instance restarts and reopens its (freshly re-enumerated) device.
    time.sleep(3)
    motion_status = _apply_to_motion(CAMERAS)
    return jsonify({"ok": True, "motion": motion_status})

@app.route("/api/admin/reboot", methods=["POST"])
def api_admin_reboot():
    """Reboot the host.

    First confirm the passwordless rule is in place (without rebooting), then
    fire `reboot` without waiting so this HTTP response can flush before systemd
    tears the service down. The browser treats the dropped connection as success.
    """
    check = subprocess.run(
        ["sudo", "-n", "-l", "reboot"],
        capture_output=True, timeout=5
    )
    if check.returncode != 0:
        return jsonify({"error": "reboot not permitted without a password — check sudoers"}), 500

    subprocess.Popen(["sudo", "-n", "reboot"])
    return jsonify({"ok": True})

def _sync_unassigned_motion():
    """On startup, stop the Motion instance for any slot with no camera assigned
    so it doesn't restart-loop on an absent device. Assigned instances are left
    untouched (systemd starts them), so working streams aren't interrupted when
    the app restarts."""
    for c in CAMERAS:
        if not c.get("device"):
            try:
                subprocess.run(
                    ["sudo", "-n", "systemctl", "stop", MOTION_UNIT.format(id=c["id"])],
                    timeout=15, capture_output=True
                )
            except Exception:
                pass


if __name__ == "__main__":
    if FRESH_SEED or MIGRATED:
        # First run, or we just migrated devices to stable port paths: push the
        # current devices into Motion (rewrite configs, restart assigned
        # instances, stop unassigned ones) so streams use the stable paths.
        _apply_to_motion(CAMERAS)
    else:
        _sync_unassigned_motion()
    # Listen on all interfaces so you can reach it from other devices on the LAN
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
