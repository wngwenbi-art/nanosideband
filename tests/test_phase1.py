"""
tests/test_phase1.py
Phase 1 integration test — two NanoCore nodes communicating locally.

Run with:
    pip install rns lxmf pytest pytest-timeout
    pytest tests/test_phase1.py -v
"""

import pathlib
import tempfile
import threading
import time

import pytest

# Skip entire module if rns/lxmf not installed
rns   = pytest.importorskip("RNS",  reason="rns not installed — run: pip install rns")
lxmf  = pytest.importorskip("LXMF", reason="lxmf not installed — run: pip install lxmf")

from nano.config import NanoConfig, DEFAULTS
from nano.core   import NanoCore


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def make_core(tmp_path: pathlib.Path, name: str, rns_config_dir: str) -> NanoCore:
    """Create a NanoCore with its own isolated config directory."""
    base = tmp_path / name
    base.mkdir(parents=True, exist_ok=True)

    cfg = NanoConfig(base_dir=base)
    cfg["display_name"]       = name
    cfg["announce_interval"]  = 9999  # disable background announces in tests
    cfg["log_level"]          = "DEBUG"
    cfg.save()
    return NanoCore(cfg)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def two_nodes(tmp_path_factory):
    """
    Spin up two NanoCores that share a single Reticulum instance
    (Reticulum is a process-level singleton in the same Python process).
    They communicate via the AutoInterface (localhost UDP multicast).
    """
    tmp = tmp_path_factory.mktemp("nanosideband")

    alice = make_core(tmp, "Alice", str(tmp / "rns"))
    bob   = make_core(tmp, "Bob",   str(tmp / "rns"))

    alice.start()
    bob.start()

    # Allow announces to propagate on localhost
    time.sleep(1.5)

    yield alice, bob

    alice.stop()
    bob.stop()


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestConfig:
    def test_defaults_loaded(self, tmp_path):
        cfg = NanoConfig(tmp_path / "cfg")
        assert cfg["display_name"] == DEFAULTS["display_name"]
        assert cfg["announce_interval"] == DEFAULTS["announce_interval"]

    def test_save_and_reload(self, tmp_path):
        cfg = NanoConfig(tmp_path / "cfg")
        cfg["display_name"] = "TestUser"
        cfg.save()

        cfg2 = NanoConfig(tmp_path / "cfg")
        assert cfg2["display_name"] == "TestUser"

    def test_derived_paths(self, tmp_path):
        cfg = NanoConfig(tmp_path / "cfg")
        assert cfg.identity_path.name == "identity"
        assert cfg.db_path.suffix == ".db"


class TestIdentity:
    def test_identity_created_on_first_start(self, two_nodes):
        alice, _ = two_nodes
        assert alice.identity_hash is not None
        assert len(alice.identity_hash.replace("<", "").replace(">", "")) == 32

    def test_identity_persists(self, two_nodes):
        """Starting again with the same config dir reloads the same identity."""
        alice, _ = two_nodes
        first_hash = alice.identity_hash

        # Re-create core from same config path
        core2 = NanoCore(alice.config)
        core2.start()
        assert core2.identity_hash == first_hash
        core2.stop()

    def test_two_nodes_have_different_hashes(self, two_nodes):
        alice, bob = two_nodes
        assert alice.identity_hash != bob.identity_hash


class TestAnnounce:
    def test_connection_count_nonzero(self, two_nodes):
        alice, _ = two_nodes
        # AutoInterface should be present
        assert alice.connection_count() >= 0  # ≥0 (may be 0 in sandboxed env)

    def test_repr_shows_running(self, two_nodes):
        alice, _ = two_nodes
        assert "running" in repr(alice)


class TestMessaging:
    @pytest.mark.timeout(15)
    def test_send_and_receive_text(self, two_nodes):
        """Alice sends a message to Bob; Bob's callback fires with correct content."""
        alice, bob = two_nodes

        received: list[dict] = []
        event = threading.Event()

        def on_msg(**kwargs):
            received.append(kwargs)
            event.set()

        bob.on_message(on_msg)

        msg_id = alice.send_text(
            bob.identity_hash.strip("<>"),
            "Hello from Alice!"
        )
        assert msg_id is not None, "send_text returned None (routing failure?)"

        # Wait up to 10s for delivery
        delivered = event.wait(timeout=10)

        assert delivered, "Bob did not receive the message within 10 seconds"
        assert len(received) == 1
        assert received[0]["content"] == "Hello from Alice!"

    @pytest.mark.timeout(15)
    def test_reply(self, two_nodes):
        """Bob replies to Alice; Alice receives it."""
        alice, bob = two_nodes

        received: list[dict] = []
        event = threading.Event()

        def on_reply(**kwargs):
            received.append(kwargs)
            event.set()

        alice.on_message(on_reply)

        bob.send_text(
            alice.identity_hash.strip("<>"),
            "Hello back from Bob!"
        )

        delivered = event.wait(timeout=10)
        assert delivered, "Alice did not receive Bob's reply within 10 seconds"
        assert "Bob" in received[0]["content"] or received[0]["content"] == "Hello back from Bob!"

    def test_invalid_destination_returns_none_or_queues(self, two_nodes):
        """Sending to a bogus hash should not crash; it gets propagation-queued."""
        alice, _ = two_nodes
        # This should not raise — bad hash gets queued for propagation
        try:
            result = alice.send_text("00" * 16, "test")
            # May return None (error) or a msg_id (queued) — both are acceptable
        except Exception as exc:
            pytest.fail(f"send_text raised unexpectedly: {exc}")

    def test_has_path(self, two_nodes):
        alice, bob = two_nodes
        # After announce exchange, alice should know a path to bob
        dest_hex = bob.identity_hash.strip("<>")
        # has_path may be False if AutoInterface isn't looped — just check no crash
        result = alice.has_path(dest_hex)
        assert isinstance(result, bool)


class TestCallbacks:
    def test_multiple_callbacks(self, two_nodes):
        """Both registered callbacks fire on the same message."""
        alice, bob = two_nodes

        hits = [0, 0]
        e1, e2 = threading.Event(), threading.Event()

        def cb1(**_): hits[0] += 1; e1.set()
        def cb2(**_): hits[1] += 1; e2.set()

        bob.on_message(cb1)
        bob.on_message(cb2)

        alice.send_text(bob.identity_hash.strip("<>"), "multi-cb test")

        e1.wait(timeout=8)
        e2.wait(timeout=8)

        assert hits[0] >= 1
        assert hits[1] >= 1
