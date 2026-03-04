"""
nano/config.py
Reads and writes ~/.nanosideband/config.toml.
Falls back to sensible defaults for every key.
"""

import sys
import os
import pathlib
import logging

# tomllib is stdlib in Python 3.11+; tomli is the backport for 3.10
if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomllib          # type: ignore
    except ImportError:
        import tomli as tomllib  # type: ignore

log = logging.getLogger(__name__)

# ── Defaults ──────────────────────────────────────────────────────────────────

DEFAULTS: dict = {
    "display_name":       "NanoUser",
    "announce_interval":  360,          # seconds between auto-announces
    "propagation_node":   "",           # hex hash of preferred prop node (empty = auto)
    "ui_mode":            "tui",        # "tui" | "web" | "headless"
    "web_port":           8080,
    "web_host":           "127.0.0.1",
    "log_level":          "WARNING",
    "max_image_px":       800,          # longest side before compression
    "image_quality":      75,           # JPEG quality 1-95
    "inline_image_limit": 51200,        # bytes; above this → RNS Resource transfer
}


# ── NanoConfig ────────────────────────────────────────────────────────────────

class NanoConfig:
    """
    Thin wrapper around a TOML config file.

    Usage:
        cfg = NanoConfig()          # uses default path
        cfg = NanoConfig("/tmp/my") # custom base directory
        print(cfg["display_name"])
        cfg["display_name"] = "Alice"
        cfg.save()
    """

    def __init__(self, base_dir: str | pathlib.Path | None = None):
        if base_dir is None:
            base_dir = pathlib.Path.home() / ".nanosideband"
        self.base_dir = pathlib.Path(base_dir)
        self.config_path = self.base_dir / "config.toml"
        self._data: dict = dict(DEFAULTS)
        self._load()

    # ── internal ──────────────────────────────────────────────────────────────

    def _load(self) -> None:
        """Read config file if it exists; merge with defaults."""
        if self.config_path.exists():
            try:
                with open(self.config_path, "rb") as f:
                    on_disk = tomllib.load(f)
                self._data.update(on_disk)
                log.debug("Config loaded from %s", self.config_path)
            except Exception as exc:
                log.warning("Could not read config (%s); using defaults.", exc)

    def _ensure_dir(self) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True)

    # ── public API ────────────────────────────────────────────────────────────

    def save(self) -> None:
        """Write current config back to disk as TOML."""
        self._ensure_dir()
        lines = ["# NanoSideband configuration\n"]
        for key, value in self._data.items():
            if isinstance(value, str):
                lines.append(f'{key} = "{value}"\n')
            elif isinstance(value, bool):
                lines.append(f'{key} = {"true" if value else "false"}\n')
            else:
                lines.append(f"{key} = {value}\n")
        self.config_path.write_text("".join(lines))
        log.debug("Config saved to %s", self.config_path)

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def __getitem__(self, key: str):
        return self._data[key]

    def __setitem__(self, key: str, value) -> None:
        self._data[key] = value

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def __repr__(self) -> str:
        return f"NanoConfig({self.config_path})"

    # ── derived paths ─────────────────────────────────────────────────────────

    @property
    def identity_path(self) -> pathlib.Path:
        return self.base_dir / "identity"

    @property
    def storage_path(self) -> pathlib.Path:
        p = self.base_dir / "storage"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def images_path(self) -> pathlib.Path:
        p = self.base_dir / "images"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def db_path(self) -> pathlib.Path:
        return self.base_dir / "nano.db"
