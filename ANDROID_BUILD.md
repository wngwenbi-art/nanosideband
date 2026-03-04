# NanoSideband Android Build

## Quick Start — Build APK via GitHub

1. **Fork or push this repo to GitHub**
2. Go to **Actions** tab → **Build Android APK**
3. Click **Run workflow** → **Run workflow** (green button)
4. Wait ~60-90 minutes for build to complete
5. Download the APK from the **Artifacts** section of the completed run
6. Install on your phone: `Settings → Install unknown apps → allow`

---

## Automatic builds

The APK builds automatically whenever you push to `main` or `master`.

To create a release build with a download link:
```
git tag v0.5.0
git push origin v0.5.0
```
This triggers a GitHub Release with the APK attached.

---

## RNode Bluetooth Setup (in-app)

1. **Pair your RNode** with your phone via Android Bluetooth settings first
2. Open NanoSideband → tap **📻** (RNode button) in the top bar
3. Tap **Refresh devices** — your RNode appears in the list
4. Select it, set your frequency/bandwidth/SF/TX power
5. Tap **Save & Connect** — the app restarts RNS with the RNode interface

### Typical RNode settings

| Region       | Frequency | BW    | SF | TXP |
|-------------|-----------|-------|----|-----|
| Europe       | 868.0 MHz | 125   | 8  | 14  |
| USA/Canada   | 915.0 MHz | 125   | 8  | 17  |
| Australia    | 915.0 MHz | 125   | 8  | 17  |
| Asia         | 433.0 MHz | 125   | 8  | 17  |
| Long range   | any       | 62.5  | 11 | max |
| High speed   | any       | 500   | 7  | max |

---

## App Structure

```
nanosideband/
├── sbapp/
│   └── main.py          ← Kivy Android UI
├── nano/
│   ├── core.py          ← RNS + LXMF core
│   ├── db.py            ← SQLite storage
│   ├── image.py         ← Image compression
│   ├── config.py        ← Config management
│   ├── tui.py           ← Terminal UI (desktop)
│   └── webui.py         ← Flask web UI (desktop)
├── buildozer.spec       ← Android build config
└── .github/workflows/
    └── build.yml        ← GitHub Actions CI
```

---

## Desktop usage (no build needed)

```bash
# Terminal UI
python -m nano --tui

# Web UI (open http://localhost:5000 in browser or phone)
pip install flask
python -m nano --web
```

---

## Troubleshooting builds

**Build fails with "Cython error"**
→ The `cython==0.29.37` pin in the workflow is intentional. Do not upgrade.

**Build fails at NDK download**
→ GitHub runner ran out of disk space. This is rare but can happen.
  Re-run the workflow — it usually succeeds on retry thanks to caching.

**APK installs but crashes on launch**
→ Download the build log artifact and look for Python import errors.

**RNode not appearing in device list**
→ Make sure it's paired in Android Bluetooth settings before opening the app.
  Some RNodes need to be powered on during pairing.

**"Permission denied" on Bluetooth**
→ Android 12+ requires explicit BLUETOOTH_CONNECT permission at runtime.
  The app requests this on startup — tap "Allow" on the system dialog.
