# ATOMICam

A lightweight, web-based viewer for up to four USB cameras on a Raspberry Pi,
built for laboratory use. ATOMICam can stream all selected cameras on a single
page, provides a dedicated single-camera view with adjustable measurement
overlays, and is served entirely from the Raspberry Pi — no internet required
after installation.

## Features

- Live **grid view** of up to four USB cameras, with a full-screen **dedicated
  view** for any one camera.
- Per-camera **image controls** (resolution, orientation, brightness, contrast,
  saturation, and more) via V4L2.
- Measurement **reticle** with adjustable size, location, color, and style.
- Web-based **camera setup** to select detected cameras and assign, label, or
  clear each camera slot.
- **Recovery tools** to reset a stuck camera, powercycle the entire USB bus,
  or reboot the Raspberry Pi remotely.
- Runs **offline** without need for an internet connection — only local
  streams, no external resources.

## Requirements

- Raspberry Pi 4B running Raspberry Pi OS (Bullseye [2021] or later).
- One to four USB (UVC) cameras.
- Installed automatically by the installer: `motion`, `uhubctl`, `v4l-utils`,
  and Flask (`python3-flask`).

## Installation

Clone the repository onto the Raspberry Pi and run the installer as root:

```bash
git clone https://github.com/markhanhardt/atomicam.git
cd atomicam
sudo bash install.sh
```

(If git is not installed on the Raspberry Pi, it can be installed via: `sudo apt install git`.)

The installer creates a dedicated `atomicam` service user, installs the app and
its systemd services, writes per-camera Motion configs, configures scoped
passwordless `sudo` for the recovery actions, and starts everything. When
installation is completed, the ATOMICam viewer page address is displayed,
typically:

```
http://<raspberrypi-ip>:5000
```

It is safe to re-run the installation script; existing camera configuration is
preserved.

## Usage

Open the printed URL in any browser on the same network.

- **Grid view** — every connected camera at once; click a tile to open the
  dedicated view.
- **Dedicated view** — one camera full-screen, with image controls, the
  measurement reticle, orientation, and resolution.
- **Admin tools** — detect and assign cameras to slots, rename them, and
  utilize the recovery tools.

Cameras, labels, rotation, resolution, and reticles are all configured from the
web interface — after installation you shouldn't need to use the terminal.

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
