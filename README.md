# Hybrid Training: installable web app

Static files, no build step. Host this folder on any HTTPS static host
(GitHub Pages, Netlify), open the URL in Safari on iPhone, then
Share > Add to Home Screen.

- `index.html`: the whole app (built from `mockup/hybrid-training-mockup.html`, starts empty).
- `sw.js`: offline cache. Bump `VERSION` (and `APP_VERSION` in index.html) on every change.
- `manifest.webmanifest`, `icons/`: home screen name and icon.
- `garmin.js`: Send to Garmin (Settings › Garmin). Talks to the private helper in `helper/`.
- `helper/`: private service that puts planned workouts on the Garmin Connect calendar. See `helper/README.md`.

Data lives in the app's localStorage under `hybrid-training-v1`. The home screen
app has its own storage, separate from Safari. Backups: Settings > Export backup.

## Install on iPhone

Open https://andreapagnini.github.io/hybrid-training-app/ in Safari, tap Share, then Add to Home Screen.

---

© 2026 Andrea Pagnini. All rights reserved. No licence is granted to copy, modify or redistribute this code.
