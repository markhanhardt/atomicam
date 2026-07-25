# Changelog

All notable changes to ATOMICam are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/), and the project aims to follow
[Semantic Versioning](https://semver.org/) (MAJOR.MINOR.PATCH).

## [1.1.0] - 2026-07-25

### Added
- The app version is now shown subtly in the interface header.

### Fixed
- Detection now excludes the Pi's on-SoC video devices (`bcm2835-codec`,
  `bcm2835-isp`, `rpi-hevc-dec`) by requiring a USB connection, so only real
  cameras are detected, seeded, and listed.  Detection also excludes metadata
  nodes.
- On a fresh install, the viewer no longer shows phantom cameras or grey frames
  when fewer than four (or no) cameras are connected — only attached cameras
  appear.
- The Admin device dropdowns now show the correct device selected for each
  camera on first load, so "Save & Apply" no longer reassigns cameras to the
  wrong devices.
- The measurement reticle now appears immediately when entering the Dedicated
  view from the tab (previously it only showed after switching cameras).
- The reticle opacity slider is now labelled.

### Changed
- On first boot the app detects connected cameras and provisions Motion to
  match, assigning only attached cameras and stopping unused instances.
- Removed the "Detect Connected Cameras" button; the device list now refreshes
  automatically whenever the Admin tab is opened.
- Re-running `install.sh` now restarts the web service, so it doubles as the
  update mechanism.
- Renamed "Admin Options" to "Admin Tools".
- Camera configuration now syncs between viewers open on other computers.

## [1.0.0] - 2026-07-20

### Added
- Per-camera rotation, resolution, and opacity controls.
- Admin tools including camera configuration reset, USB bus power cycle,
  Raspberry Pi reboot, and web-based camera setup.
- Now have the ability to name and assign cameras.
- Built installer and developed file structure for installation via GitHub.

### Fixed
- The viewer page no longer needs to be manually refreshed after losing camera
  connection.  A heartbeat checks for connection and automatically refreshes
  when found.
- Cleaned up unneeded code.

### Changed
- Renamed project from CASPAR-Cam to ATOMICam.
- Made camera settings and reticles persistent between reboots/viewer sessions.
- Made reticles visible in grid view.

## [0.1.0] - 2026-04-01

- Initial prototype: multi-camera live viewer with grid and dedicated views,
  per-camera image controls, measurement reticles.
