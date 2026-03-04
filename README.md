# NanoSideband

A lightweight, text-and-image-only LXMF messaging client built on the
[Reticulum Network Stack](https://reticulum.network).

Fully interoperable with Sideband, Nomad Network, and all LXMF clients.

---

## Quick install

```bash
pip install rns lxmf pillow textual flask
git clone <this-repo> nanosideband
cd nanosideband
```

## Phase 1 — headless

```bash
# Print your identity hash
python -m nano --whoami

# Listen for incoming messages (prints to stdout)
python -m nano --listen

# Send a single message
python -m nano --send <destination-hash> "Hello!"

# Run as background daemon
python -m nano --headless

# Set your display name
python -m nano --name "Alice"
```

## Run the smoke test

```bash
python scripts/smoke_test.py
```

## Run tests

```bash
pip install pytest pytest-timeout
pytest tests/ -v
```

---

## Architecture

```
nano/
├── __init__.py       version
├── __main__.py       CLI entry point
├── config.py         TOML config (~/.nanosideband/config.toml)
├── core.py           NanoCore — RNS + LXMF wiring        ← Phase 1
├── db.py             SQLite storage                        ← Phase 2
├── image.py          Pillow compress + LXMF field pack     ← Phase 3
├── tui.py            Textual terminal UI                   ← Phase 4
└── webui.py          Flask web UI                          ← Phase 5
```

## Config file

Located at `~/.nanosideband/config.toml`:

```toml
display_name = "Alice"
announce_interval = 360
propagation_node = ""   # hex hash, or empty for auto
ui_mode = "tui"
web_port = 8080
web_host = "127.0.0.1"
log_level = "WARNING"
max_image_px = 800
image_quality = 75
inline_image_limit = 51200
```

## Interoperability

NanoSideband uses the standard `rns` + `lxmf` packages with no protocol
modifications, so it talks to any LXMF-capable node: Sideband (Android/desktop),
Nomad Network, MeshChat, or another NanoSideband instance.

## Build phases

| Phase | What | Status |
|-------|------|--------|
| 1 | RNS + LXMF core, headless send/receive | ✅ **Complete** |
| 2 | SQLite contacts + message store | ⬜ Next |
| 3 | Image compress + LXMF Fields transfer | ⬜ |
| 4 | Textual TUI | ⬜ |
| 5 | Flask WebUI | ⬜ |
