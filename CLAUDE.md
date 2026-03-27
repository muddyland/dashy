# Dashy - Development Notes

Dashy is a web application that downloads and serves locked video clips from a Viofo dashcam (tested with A129-Plus Duo and A229-Plus). It runs on a Raspberry Pi connected via LAN, with the dashcam connecting via its WiFi hotspot.

## Suggested Improvements

### High Priority

- ~~**Replace JSON files with SQLite for queue/history**~~ — Done. `DownloadsDB` in `viofo.py` now uses `dashy.db` (SQLite) with `downloaded` and `queue` tables. Existing JSON files are auto-migrated and renamed to `.migrated` on first startup.

- ~~**Download progress in the web UI**~~ — Done. `/api/progress` reads a `progress` table in `dashy.db` written by `Downloads.download_video()` every 1024 chunks. `downloads.js` polls both `/api/queue` and `/api/progress` every 5s and renders a Bootstrap progress bar in the queue dropdown for the active file.

- **Human-readable video metadata in the UI** — The filename parser already extracts timestamp, front/rear, and parking/driving mode, but the UI shows raw filenames. Format cards as e.g. "Driving - Front | Mar 25, 2026 14:32".

- ~~**Free space check before downloading**~~ — Done. `Downloads.download_video()` checks `shutil.disk_usage()` before starting the download loop and aborts with an error log if less than 1 GB is free.

### Medium Priority

- **Notification on new downloads** — Fire a Home Assistant webhook (or Gotify/Apprise) when new locked clips are downloaded, so you know when the dashcam locked a clip.

- ~~**Configurable retention / auto-deletion**~~ — Done. `cleanup_old_files()` in `dashy.py` runs after each download cycle. Controlled by `retention_enabled` (default: `true`) and `retention_days` (default: `180`) in `config.json`. Set `retention_enabled` to `false` to keep everything forever.

- ~~**Live stream subprocess cleanup**~~ — Done. `generate_video_frames()` in `viofo.py` now wraps the read loop in `try/finally`, calling `ffmpeg_process.kill()` and `wait()` on teardown.

- **Camera proxy authentication** — The camera proxy exposes the dashcam's web server with no auth. Add basic auth to that route if Dashy is reachable by others on the network.

### Low Priority / Nice-to-Have

- **Bulk download from camera UI** — The `/cam/locked` view queues one file at a time. Add a "Download All" button to queue all visible locked clips at once.

- **In-browser video playback** — Serve videos in a `<video>` tag modal with the thumbnail as poster, rather than as a file download. Browsers can play dashcam MP4s natively.

- **GPS/map overlay** — Viofo cameras embed GPS metadata in video files. If available, render a route on a Leaflet + OpenStreetMap map alongside playback.
