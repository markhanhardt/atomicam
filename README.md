# ATOMICam

A lightweight, web-based viewer for up to four USB cameras on a Raspberry Pi,
built for laboratory use. It streams all connected cameras on a single page,
lets you open any one full-screen with adjustable measurement overlays, and is
served entirely from the Pi — no internet required after installation.

## Features

- Live **grid view** of up to four USB cameras, with a full-screen **dedicated
  view** for any one.
- Per-camera **image controls** (brightness, contrast, saturation, and more)
  via V4L2.
- Adjustable measurement **reticle** — three styles, plus color, size,
  position, and transparency — saved per camera.
- Per-camera **rotation** (0/90/180/270°) and **resolution**, set from the web
  UI.
- Web-based **camera setup**: detect connected cameras and assign, label, or
  clear each slot — no terminal needed after install.
- **Recovery tools**: a hardware-independent camera reset, a hard USB
  power-cycle, and a reboot.
- Runs **offline** — only local streams, no external resources.

## Requirements

- Raspberry Pi running Raspberry Pi OS (Bookworm or Bullseye).
- One to four USB (UVC) cameras.
- Installed automatically by the installer: `motion`, `uhubctl`, `v4l-utils`,
  and Flask (`python3-flask`).

## Installation

Clone the repository onto the Pi and run the installer as root:

```bash
git clone https://github.com/markhanhardt/atomicam.git
cd atomicam
sudo bash install.sh
```

The installer creates a dedicated `atomicam` service user, installs the app and
its systemd services, writes per-camera Motion configs, configures scoped
passwordless `sudo` for the recovery actions, and starts everything. When it
finishes it prints the address to open, typically:

```
http://<pi-ip>:5000
```

It is safe to re-run; existing camera configuration is preserved.

## Usage

Open the printed URL in any browser on the same network.

- **Grid view** — every connected camera at once; click a tile to open it
  full-screen.
- **Dedicated view** — one camera full-screen, with image controls, the
  measurement reticle, orientation, and resolution.
- **Admin** — detect and assign cameras to slots, rename them, and reach the
  recovery tools.

Cameras, labels, rotation, resolution, and reticles are all configured from the
web interface — after installation you shouldn't need the terminal.

## Configuration & data

- Camera assignments and reticles: `/var/lib/atomicam/cameras.json`
- Motion per-camera configs: `/etc/motion/cameras/cam0.conf … cam3.conf`
- Services: `ATOMICam.service` (web UI) and `ATOMICam-motion@0…3.service`
  (streams)
- Logs: `journalctl -u ATOMICam -f` and `/var/log/motion/cam*.log`

## Recovery actions (Admin tab)

- **Reset Cameras** — re-enumerates all USB cameras through the kernel; works on
  any hardware. Try this first.
- **Hard Power-Cycle USB** — physically cuts USB power via `uhubctl` (requires a
  hub whose controller supports power switching).
- **Reboot Raspberry Pi** — restarts the host.

## Per-machine notes

- **Motion configs** are generated from a working template, but directive names
  vary between Motion versions. If a camera won't stream, check its log and
  compare its config against a known-good one for your Motion version.
- **USB hub location**: the hard power-cycle uses `uhubctl -l 1-1`. If your Pi
  enumerates its hub differently, that action may need adjusting — the software
  "Reset Cameras" action is location-independent and unaffected.

## Uninstalling

```bash
sudo bash uninstall.sh            # remove the app, keep camera configuration
sudo bash uninstall.sh --purge    # remove everything, including configuration
```

## License

Released under the MIT License — see [LICENSE](LICENSE).
