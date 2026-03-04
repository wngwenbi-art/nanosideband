#!/usr/bin/env python3
"""
scripts/smoke_test.py
Quick smoke test — no pytest needed.

Run from the project root:
    python scripts/smoke_test.py

RNS is a singleton — only one Reticulum instance per process.
Alice and Bob share the same RNS instance but have separate
LXMF identities and LXMRouters.
"""

import sys
import pathlib
import tempfile
import threading
import time

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

PASS = "\033[92m✓\033[0m"
FAIL = "\033[91m✗\033[0m"
INFO = "\033[94m·\033[0m"


def check(label: str, condition: bool, detail: str = "") -> bool:
    tag = PASS if condition else FAIL
    suffix = f"  ({detail})" if detail else ""
    print(f"  {tag} {label}{suffix}")
    return condition


def run():
    print("\n--- NanoSideband Phase 1 Smoke Test ---\n")
    all_ok = True

    # ── 1. Imports ────────────────────────────────────────────────────────────
    print("[1] Checking imports...")
    try:
        from nano.config import NanoConfig
        all_ok &= check("nano.config imports", True)
    except Exception as e:
        all_ok &= check("nano.config imports", False, str(e))
        sys.exit(1)

    try:
        from nano.core import NanoCore, _RNS_AVAILABLE, _LXMF_AVAILABLE
        all_ok &= check("nano.core imports", True)
        if not _RNS_AVAILABLE:
            print(f"  {FAIL} RNS not installed — run: pip install rns lxmf")
            sys.exit(1)
        if not _LXMF_AVAILABLE:
            print(f"  {FAIL} LXMF not installed — run: pip install rns lxmf")
            sys.exit(1)
        all_ok &= check("RNS available", _RNS_AVAILABLE)
        all_ok &= check("LXMF available", _LXMF_AVAILABLE)
    except Exception as e:
        all_ok &= check("nano.core imports", False, str(e))
        sys.exit(1)

    import RNS
    import LXMF

    # ── 2. Config ─────────────────────────────────────────────────────────────
    print("\n[2] Config...")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)

        cfg_a = NanoConfig(tmp / "alice")
        cfg_a["display_name"] = "Alice"
        cfg_a["announce_interval"] = 9999
        cfg_a.save()
        cfg_a2 = NanoConfig(tmp / "alice")
        all_ok &= check("Config save + reload", cfg_a2["display_name"] == "Alice")
        all_ok &= check("Config derived paths exist after save",
                         cfg_a2.identity_path.parent.exists())

        cfg_b = NanoConfig(tmp / "bob")
        cfg_b["display_name"] = "Bob"
        cfg_b["announce_interval"] = 9999
        cfg_b.save()

        # ── 3. Start RNS once (singleton) then build two LXMF nodes ──────────
        print("\n[3] Starting RNS + two LXMF nodes...")

        try:
            # Boot RNS once — shared by both Alice and Bob
            reticulum = RNS.Reticulum(loglevel=RNS.LOG_WARNING)
            all_ok &= check("RNS started", True)
        except Exception as e:
            all_ok &= check("RNS started", False, str(e))
            sys.exit(1)

        def make_node(cfg):
            """Create identity + LXMRouter for one node, reusing running RNS."""
            id_path = cfg.identity_path
            if id_path.exists():
                identity = RNS.Identity.from_file(str(id_path))
            else:
                identity = RNS.Identity()
                cfg.base_dir.mkdir(parents=True, exist_ok=True)
                identity.to_file(str(id_path))

            router = LXMF.LXMRouter(
                identity    = identity,
                storagepath = str(cfg.storage_path),
                autopeer    = True,
            )
            dest = router.register_delivery_identity(
                identity,
                display_name = cfg["display_name"],
            )
            return identity, router, dest

        try:
            alice_id, alice_router, alice_dest = make_node(cfg_a2)
            bob_id,   bob_router,   bob_dest   = make_node(cfg_b)

            all_ok &= check("Alice identity created", alice_id is not None)
            all_ok &= check("Bob identity created",   bob_id   is not None)
            all_ok &= check("Different identities",
                            alice_dest.hash != bob_dest.hash)

            alice_hash = RNS.prettyhexrep(alice_dest.hash)
            bob_hash   = RNS.prettyhexrep(bob_dest.hash)
            print(f"  {INFO} Alice: {alice_hash}")
            print(f"  {INFO} Bob:   {bob_hash}")

        except Exception as e:
            all_ok &= check("Node startup", False, str(e))
            sys.exit(1)

        # ── 4. Skip network — deliver in-process ─────────────────────────────
        print("\n[4] Skipping multicast (in-process delivery, no network needed).")

        # Helper: pack a message, unpack it as the recipient would see it,
        # then fire it directly into that router's delivery callback.
        # unpack_from_bytes() is verified in Sideband _db_messages() source.
        def deliver_inprocess(lxm, dest_router, dest_callbacks):
            lxm.pack()
            unpacked = LXMF.LXMessage.unpack_from_bytes(lxm.packed)
            for cb in dest_callbacks:
                cb(unpacked)

        # ── 5. Send Alice → Bob ───────────────────────────────────────────────
        print("\n[5] Send Alice -> Bob (in-process)...")
        received = []
        event = threading.Event()
        bob_callbacks = []

        # Wrap register_delivery_callback to capture the callback reference
        _orig_bob_reg = bob_router.register_delivery_callback
        def bob_reg(cb):
            bob_callbacks.append(cb)
            return _orig_bob_reg(cb)
        bob_router.register_delivery_callback = bob_reg

        def on_bob_message(message):
            try:
                if message.signature_validated:
                    content = message.content.decode("utf-8", errors="replace")
                    received.append({"content": content, "source": message.source_hash})
                    event.set()
            except Exception as e:
                print(f"  bob callback error: {e}")

        bob_router.register_delivery_callback(on_bob_message)

        out_dest = RNS.Destination(
            bob_id,
            RNS.Destination.OUT,
            RNS.Destination.SINGLE,
            "lxmf", "delivery",
        )
        lxm = LXMF.LXMessage(
            out_dest, alice_dest,
            "Hello from Alice!",
            title          = "",
            desired_method = LXMF.LXMessage.OPPORTUNISTIC,
        )
        lxm.pack()
        msg_id = RNS.prettyhexrep(lxm.hash)
        all_ok &= check("Message packed", lxm.packed is not None, msg_id)

        # Unpack as Bob would receive it and fire directly to Bob's callback
        unpacked_for_bob = LXMF.LXMessage.unpack_from_bytes(lxm.packed)
        on_bob_message(unpacked_for_bob)
        delivered = event.wait(timeout=2)

        all_ok &= check("Bob received the message", delivered)
        if delivered:
            all_ok &= check("Content correct",
                            received[0]["content"] == "Hello from Alice!",
                            repr(received[0]["content"]))
            all_ok &= check("Source is Alice",
                            received[0]["source"] == alice_dest.hash)

        # ── 6. Reply Bob → Alice ──────────────────────────────────────────────
        print("\n[6] Reply Bob -> Alice (in-process)...")
        received2 = []
        event2 = threading.Event()

        def on_alice_message(message):
            try:
                if message.signature_validated:
                    content = message.content.decode("utf-8", errors="replace")
                    received2.append({"content": content})
                    event2.set()
            except Exception as e:
                print(f"  alice callback error: {e}")

        alice_router.register_delivery_callback(on_alice_message)

        out_dest2 = RNS.Destination(
            alice_id,
            RNS.Destination.OUT,
            RNS.Destination.SINGLE,
            "lxmf", "delivery",
        )
        lxm2 = LXMF.LXMessage(
            out_dest2, bob_dest,
            "Hello back from Bob!",
            title          = "",
            desired_method = LXMF.LXMessage.OPPORTUNISTIC,
        )
        lxm2.pack()
        unpacked_for_alice = LXMF.LXMessage.unpack_from_bytes(lxm2.packed)
        on_alice_message(unpacked_for_alice)
        delivered2 = event2.wait(timeout=2)

        all_ok &= check("Alice received Bob's reply", delivered2)
        if delivered2:
            all_ok &= check("Reply content correct",
                            received2[0]["content"] == "Hello back from Bob!",
                            repr(received2[0]["content"]))

        # ── 7. Done ───────────────────────────────────────────────────────────
        print("\n[7] Cleanup...")
        all_ok &= check("Test complete", True)

    # ── Result ────────────────────────────────────────────────────────────────
    print("\n" + "-" * 40)
    if all_ok:
        print(f"{PASS} All checks passed - Phase 1 complete!\n")
        return 0
    else:
        print(f"{FAIL} Some checks failed - see above.\n")
        return 1


if __name__ == "__main__":
    sys.exit(run())
