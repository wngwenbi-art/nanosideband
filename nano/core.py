"""
nano/core.py
NanoCore — verified against real RNS + LXMF + Sideband source code.

Key corrections from source inspection:
  - LXMRouter(identity=, storagepath=, autopeer=, delivery_limit=)
  - register_delivery_callback() is a SEPARATE call from register_delivery_identity()
  - LXMessage(dest, source, content, title="", desired_method=, fields=, include_ticket=)
  - lxm.register_delivery_callback() and lxm.register_failed_callback() are per-message
  - message.source_hash / message.destination_hash are raw bytes
  - Outbound Destination = RNS.Destination(recalled_identity, OUT, SINGLE, "lxmf", "delivery")
  - LXMF.display_name_from_app_data(app_data) extracts display name from announce
  - lxmf_destination.announce() for simple announce (no stamps)
  - RNS.Transport.request_path(dest_hash) for path discovery
  - RNS.prettyhexrep() returns <hexstring> -- strip < > when parsing user input
"""

import logging
import threading
import time
from typing import Callable

from nano.db import NanoDB
from nano.image import image_to_field, field_to_display, _PIL_AVAILABLE as _IMAGE_AVAILABLE

log = logging.getLogger(__name__)

_RNS_AVAILABLE  = False
_LXMF_AVAILABLE = False

try:
    import RNS
    _RNS_AVAILABLE = True
except Exception as _rns_import_err:
    print(f"[CORE] RNS import FAILED: {type(_rns_import_err).__name__}: {_rns_import_err}")
    import traceback
    traceback.print_exc()

try:
    import LXMF
    _LXMF_AVAILABLE = True
except Exception as _lxmf_import_err:
    print(f"[CORE] LXMF import FAILED: {type(_lxmf_import_err).__name__}: {_lxmf_import_err}")

APP_NAME          = "nanosideband"
LXMF_APP_NAME     = "lxmf"
LXMF_ASPECT       = "delivery"
ANNOUNCE_INTERVAL = 360


class NanoCore:
    """
    Central controller for NanoSideband.

    Lifecycle:
        core = NanoCore(config)
        core.start()
        core.send_text("a1b2c3...", "hello")
        core.stop()
    """

    def __init__(self, config):
        self.config = config

        # Named to match Sideband conventions for easy cross-reference
        self.reticulum         = None   # RNS.Reticulum instance
        self.message_router    = None   # LXMF.LXMRouter instance
        self.identity          = None   # RNS.Identity instance
        self.lxmf_destination  = None   # Our registered LXMF delivery Destination
        self.db                = NanoDB(config.db_path)

        self._running = False
        self._announce_thread: threading.Thread | None = None

        # UI callbacks
        self._message_callbacks: list[Callable] = []
        self._status_callbacks:  list[Callable] = []

        # Queue for messages sent before start()
        self._pending: list[dict] = []

    # -----------------------------------------------------------------------
    # Start / Stop
    # -----------------------------------------------------------------------

    def start(self) -> None:
        """Boot Reticulum and LXMF. Returns once both are ready."""
        if not _RNS_AVAILABLE:
            raise RuntimeError(
                "Reticulum (rns) is not installed.\n"
                "  Run:  pip install rns lxmf"
            )
        if not _LXMF_AVAILABLE:
            raise RuntimeError(
                "LXMF is not installed.\n"
                "  Run:  pip install rns lxmf"
            )

        # 0. Open database
        self.db.open()

        # 1. Boot Reticulum
        # Write a minimal config with no interfaces so RNS does NOT
        # auto-load the Android BLE interface (which crashes on able_recipe).
        # We attach our own RFCOMM interface after startup.
        import os, sys
        if hasattr(sys, 'getandroidapilevel'):
            # Running on Android — write blank config to suppress BLE interface
            from android.storage import app_storage_path  # type: ignore
            _rns_cfgdir = os.path.join(app_storage_path(), "reticulum")
            os.makedirs(_rns_cfgdir, exist_ok=True)
            _rns_cfg = os.path.join(_rns_cfgdir, "config")
            if not os.path.exists(_rns_cfg):
                with open(_rns_cfg, "w") as f:
                    f.write("[reticulum]\n  enable_transport = False\n  share_instance = False\n")
        else:
            _rns_cfgdir = None

        self.reticulum = RNS.Reticulum(
            configdir = _rns_cfgdir,
            loglevel  = RNS.LOG_WARNING,
        )
        log.info("Reticulum started.")

        # 2. Load or create persistent Identity
        self.identity = self._load_or_create_identity()
        log.info("Identity: %s", RNS.prettyhexrep(self.identity.hash))

        # 3. Start LXMRouter
        # Real constructor (from source): LXMRouter(identity=, storagepath=,
        #                                            autopeer=, delivery_limit=)
        self.message_router = LXMF.LXMRouter(
            identity    = self.identity,
            storagepath = str(self.config.storage_path),
            autopeer    = True,
        )

        # 4. Register delivery identity -> returns our lxmf_destination
        self.lxmf_destination = self.message_router.register_delivery_identity(
            self.identity,
            display_name = self.config["display_name"],
        )

        # 5. Register delivery callback (MUST be a separate call — verified)
        self.message_router.register_delivery_callback(self._on_message_received)

        log.info(
            "LXMF destination: %s",
            RNS.prettyhexrep(self.lxmf_destination.hash),
        )

        # 6. Optional: set preferred propagation node
        prop_node = self.config.get("propagation_node", "").strip()
        if prop_node:
            try:
                node_hash = bytes.fromhex(prop_node)
                self.message_router.set_outbound_propagation_node(node_hash)
                log.info("Propagation node set: %s", prop_node)
            except Exception as exc:
                log.warning("Invalid propagation node hash (%s)", exc)

        self._running = True

        # 7. Initial announce
        self._announce()

        # 8. Background announce loop
        self._announce_thread = threading.Thread(
            target = self._announce_loop,
            daemon = True,
            name   = "nano-announce",
        )
        self._announce_thread.start()

        # 9. Drain messages queued before start()
        for pending in self._pending:
            self._dispatch_text(**pending)
        self._pending.clear()

        log.info("NanoCore ready.")

    def attach_rnode_bt(self, bt_addr: str, frequency: int, bandwidth: int,
                        spreading_factor: int, tx_power: int,
                        coding_rate: int = 5) -> str:
        """
        Attach a custom RFCOMM RNode interface to the running RNS instance.
        Returns status string. Call after start().
        """
        try:
            from nano.rnode_bt import make_rns_interface
            iface = make_rns_interface(
                bt_addr=bt_addr,
                frequency=frequency,
                bandwidth=bandwidth,
                spreading_factor=spreading_factor,
                tx_power=tx_power,
                coding_rate=coding_rate,
            )
            if iface is None:
                return "Failed to create interface"
            ok = iface.start()
            if ok:
                RNS.Transport.interfaces.append(iface)
                self.rnode_interface = iface
                log.info("RNode BT interface attached: %s", bt_addr)
                return "RNode connected"
            else:
                return iface.status
        except Exception as e:
            log.error("attach_rnode_bt error: %s", e)
            return f"Error: {e}"

    def stop(self) -> None:
        self._running = False
        if hasattr(self, 'rnode_interface') and self.rnode_interface:
            try:
                self.rnode_interface.stop()
            except Exception:
                pass
        self.db.close()
        log.info("NanoCore stopped.")

    # -----------------------------------------------------------------------
    # Identity
    # -----------------------------------------------------------------------

    def _load_or_create_identity(self) -> "RNS.Identity":
        id_path = self.config.identity_path
        if id_path.exists():
            identity = RNS.Identity.from_file(str(id_path))
            log.info("Loaded identity from %s", id_path)
        else:
            identity = RNS.Identity()
            self.config.base_dir.mkdir(parents=True, exist_ok=True)
            identity.to_file(str(id_path))
            log.info("Created new identity at %s", id_path)
        return identity

    # -----------------------------------------------------------------------
    # Announce
    # -----------------------------------------------------------------------

    def announce(self) -> None:
        """Send an LXMF announce (public alias for _announce)."""
        self._announce()

    def _announce(self) -> None:
        """
        Broadcast our LXMF destination to the network.

        Sideband pattern (no stamps): self.lxmf_destination.announce()
        We pass display_name as app_data bytes so peers can read our name.
        """
        if self.lxmf_destination is None:
            return
        try:
            self.lxmf_destination.announce(
                app_data = self.config["display_name"].encode("utf-8")
            )
            log.debug("Announced as %r", self.config["display_name"])
        except Exception as exc:
            log.warning("Announce failed: %s", exc)

    def _announce_loop(self) -> None:
        interval = self.config.get("announce_interval", ANNOUNCE_INTERVAL)
        while self._running:
            time.sleep(interval)
            if self._running:
                self._announce()

    # -----------------------------------------------------------------------
    # Send
    # -----------------------------------------------------------------------

    def send_text(self, dest_hash_hex: str, content: str) -> str | None:
        """
        Send a plain-text LXMF message.

        Parameters
        ----------
        dest_hash_hex : str
            Destination hash as hex (32 chars).
            <anglebrackets> from RNS.prettyhexrep() are stripped automatically.
        content : str
            Message body.

        Returns
        -------
        str | None
            Message hash (prettyhexrep) if queued, else None.
        """
        dest_hash_hex = (
            dest_hash_hex.strip()
            .replace("<", "").replace(">", "")
            .replace(" ", "").lower()
        )

        if not self._running:
            log.debug("NanoCore not started — queuing message.")
            self._pending.append(
                {"dest_hash_hex": dest_hash_hex, "content": content}
            )
            return None

        return self._dispatch(dest_hash_hex=dest_hash_hex, content=content)

    def send_image(
        self,
        dest_hash_hex: str,
        image_source,
        caption: str = "",
    ) -> str | None:
        """
        Send an image with optional caption text.

        Parameters
        ----------
        dest_hash_hex : str
            Destination hash hex.
        image_source : str | Path | bytes
            File path or raw image bytes to compress and send.
        caption : str
            Optional text caption (becomes message content).

        Returns
        -------
        str | None
            Message hash if queued, else None.
        """
        if not _IMAGE_AVAILABLE:
            log.error("send_image requires Pillow: pip install pillow")
            return None

        dest_hash_hex = (
            dest_hash_hex.strip()
            .replace("<", "").replace(">", "")
            .replace(" ", "").lower()
        )

        img_bytes = image_to_field(
            image_source,
            max_width = self.config.get("image_max_width", 800),
            quality   = self.config.get("image_quality", 70),
        )

        if img_bytes is None:
            log.error("Image compression failed — not sending.")
            return None

        import LXMF as _LXMF
        fields = {_LXMF.FIELD_IMAGE: img_bytes}

        return self._dispatch(
            dest_hash_hex = dest_hash_hex,
            content       = caption,
            fields        = fields,
            has_image     = True,
        )

    def _dispatch(
        self,
        dest_hash_hex: str,
        content: str,
        fields: dict = None,
        has_image: bool = False,
    ) -> str | None:
        """Build and submit an LXMessage to the router."""
        if not content and not fields:
            log.error("Cannot send empty message.")
            return None

        try:
            dest_hash = bytes.fromhex(dest_hash_hex)
        except ValueError:
            log.error("Invalid destination hash: %r", dest_hash_hex)
            return None

        # Recall the remote Identity — needed to construct the Destination.
        # If unknown, kick off path discovery and use propagation delivery.
        dest_identity = RNS.Identity.recall(dest_hash)

        if dest_identity is None:
            log.info(
                "Identity for %s unknown — requesting path, "
                "falling back to propagation delivery.",
                dest_hash_hex,
            )
            RNS.Transport.request_path(dest_hash)
            desired_method = LXMF.LXMessage.PROPAGATED
        else:
            if (
                self.message_router.delivery_link_available(dest_hash)
                or RNS.Transport.has_path(dest_hash)
            ):
                desired_method = LXMF.LXMessage.DIRECT
            else:
                desired_method = LXMF.LXMessage.PROPAGATED
                log.info(
                    "No direct path to %s — using propagation.", dest_hash_hex
                )

        try:
            # Outbound Destination — exact pattern from Sideband source:
            # RNS.Destination(identity, OUT, SINGLE, "lxmf", "delivery")
            dest = RNS.Destination(
                dest_identity,
                RNS.Destination.OUT,
                RNS.Destination.SINGLE,
                LXMF_APP_NAME,
                LXMF_ASPECT,
            )

            # LXMessage — verified constructor from source:
            # LXMessage(dest, source, content, title="",
            #           desired_method=, fields=, include_ticket=)
            lxm = LXMF.LXMessage(
                dest,
                self.lxmf_destination,
                content,
                title          = "",
                desired_method = desired_method,
                fields         = fields,
            )

            # Per-message callbacks (verified pattern from Sideband source)
            lxm.register_delivery_callback(self._on_delivery_status)
            lxm.register_failed_callback(self._on_delivery_status)

            # Auto-retry via propagation if direct delivery fails
            if self.message_router.get_outbound_propagation_node() is not None:
                lxm.try_propagation_on_fail = True

            self.message_router.handle_outbound(lxm)
            msg_id = RNS.prettyhexrep(lxm.hash)
            msg_id_clean = msg_id.replace("<","").replace(">","")

            # Persist outbound message
            self.db.save_message(
                msg_hash    = msg_id_clean,
                dest_hash   = dest_hash_hex,
                source_hash = RNS.prettyhexrep(self.lxmf_destination.hash).replace("<","").replace(">",""),
                content     = content,
                timestamp   = time.time(),
                direction   = "out",
                state       = "pending",
                has_image   = has_image,
                tx_ts       = time.time(),
            )
            log.info("Queued %s → %s", msg_id, dest_hash_hex)
            return msg_id

        except Exception as exc:
            log.error("Failed to send message: %s", exc)
            return None

    # -----------------------------------------------------------------------
    # Receive
    # -----------------------------------------------------------------------

    def _on_message_received(self, message) -> None:
        """
        Called by LXMRouter for every delivered inbound message.
        Mirrors lxmf_delivery() in Sideband source.
        """
        try:
            # Validate signature — Sideband checks this first
            if not message.signature_validated:
                log.warning(
                    "Dropping message with invalid signature "
                    "(reason: %s)", message.unverified_reason
                )
                return

            src_hex = RNS.prettyhexrep(message.source_hash)

            content = ""
            if message.content:
                content = message.content.decode("utf-8", errors="replace")

            has_image = bool(
                message.fields and LXMF.FIELD_IMAGE in message.fields
            )

            # Extract display name using the real LXMF helper
            # (LXMF.display_name_from_app_data verified in source)
            display_name = ""
            app_data = RNS.Identity.recall_app_data(message.source_hash)
            if app_data is not None:
                try:
                    dn = LXMF.display_name_from_app_data(app_data)
                    if dn:
                        display_name = dn
                except Exception:
                    # Old announce format: raw UTF-8 string
                    try:
                        display_name = app_data.decode("utf-8")
                    except Exception:
                        pass

            log.info(
                "<- From %s (%s): %r%s",
                src_hex,
                display_name or "unknown",
                content[:80],
                " [+image]" if has_image else "",
            )

            # Persist to database
            src_clean = src_hex.replace("<","").replace(">","")
            msg_hash_clean = RNS.prettyhexrep(message.hash).replace("<","").replace(">","")
            self.db.touch_contact(src_clean, display_name=display_name)
            self.db.save_message(
                msg_hash    = msg_hash_clean,
                dest_hash   = RNS.prettyhexrep(self.lxmf_destination.hash).replace("<","").replace(">",""),
                source_hash = src_clean,
                content     = content,
                timestamp   = message.timestamp,
                direction   = "in",
                state       = "delivered",
                has_image   = has_image,
                rx_ts       = time.time(),
            )

            # Persist image blob if present
            if has_image:
                try:
                    raw = message.fields[LXMF.FIELD_IMAGE]
                    info = field_to_display(raw)
                    if info:
                        self.db.save_image(
                            msg_hash_clean,
                            raw,
                            mime_type = info["mime_type"],
                            width     = info["width"],
                            height    = info["height"],
                        )
                except Exception as exc:
                    log.warning("Failed to store inbound image: %s", exc)

            for cb in self._message_callbacks:
                try:
                    cb(
                        source_hash  = src_hex,
                        display_name = display_name,
                        content      = content,
                        fields       = message.fields,
                        timestamp    = message.timestamp,
                        msg_hash     = RNS.prettyhexrep(message.hash),
                        has_image    = has_image,
                    )
                except Exception as exc:
                    log.error("Message callback error: %s", exc)

        except Exception as exc:
            log.error("Error processing received message: %s", exc)

    def _on_delivery_status(self, message) -> None:
        """Called when an outbound message changes delivery state."""
        try:
            msg_id = RNS.prettyhexrep(message.hash)
            msg_id_clean = msg_id.replace("<","").replace(">","")
            state_map = {
                LXMF.LXMessage.DELIVERED: "delivered",
                LXMF.LXMessage.FAILED:    "failed",
                LXMF.LXMessage.SENT:      "sent",
            }
            status = state_map.get(message.state, f"state_{message.state}")
            log.info("Delivery %s: %s", msg_id, status)

            # Update persisted state
            self.db.update_message_state(msg_id_clean, status)

            for cb in self._status_callbacks:
                try:
                    cb(msg_id=msg_id, status=status)
                except Exception as exc:
                    log.error("Status callback error: %s", exc)

        except Exception as exc:
            log.error("Error in delivery callback: %s", exc)

    # -----------------------------------------------------------------------
    # Callback registration
    # -----------------------------------------------------------------------

    def on_message(self, callback: Callable) -> None:
        """
        Register a callback called on every received message.

        Signature:
            fn(source_hash, display_name, content, fields,
               timestamp, msg_hash, has_image)
        """
        self._message_callbacks.append(callback)

    def on_delivery_status(self, callback: Callable) -> None:
        """
        Register a callback called when a sent message changes state.

        Signature:
            fn(msg_id, status)   status in {"sent", "delivered", "failed"}
        """
        self._status_callbacks.append(callback)

    # Alias used by the TUI
    on_status = on_delivery_status

    # -----------------------------------------------------------------------
    # Properties / queries
    # -----------------------------------------------------------------------

    @property
    def identity_hash(self) -> str | None:
        """Our LXMF destination hash as prettyhexrep (<hexstring>), or None."""
        if self.lxmf_destination is None:
            return None
        return RNS.prettyhexrep(self.lxmf_destination.hash)

    @property
    def identity_hash_raw(self) -> bytes | None:
        """Raw bytes of our LXMF destination hash."""
        if self.lxmf_destination is None:
            return None
        return self.lxmf_destination.hash

    def has_path(self, dest_hash_hex: str) -> bool:
        """True if Reticulum has a known route to dest_hash_hex."""
        try:
            h = dest_hash_hex.replace("<","").replace(">","").strip()
            return RNS.Transport.has_path(bytes.fromhex(h))
        except Exception:
            return False

    def is_known(self, dest_hash_hex: str) -> bool:
        """True if we have a recalled Identity for dest_hash_hex."""
        try:
            h = dest_hash_hex.replace("<","").replace(">","").strip()
            return RNS.Identity.recall(bytes.fromhex(h)) is not None
        except Exception:
            return False

    def connection_count(self) -> int:
        """Number of active RNS transport interfaces."""
        if self.reticulum is None:
            return 0
        try:
            return len(RNS.Transport.interfaces)
        except Exception:
            return 0

    def propagation_node_hash(self) -> str | None:
        """prettyhexrep of active propagation node, or None."""
        if self.message_router is None:
            return None
        try:
            node = self.message_router.get_outbound_propagation_node()
            if node:
                return RNS.prettyhexrep(node)
        except Exception:
            pass
        return None

    def __repr__(self) -> str:
        state = "running" if self._running else "stopped"
        return f"NanoCore({state}, id={self.identity_hash})"
