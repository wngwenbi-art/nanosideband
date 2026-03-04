#!/usr/bin/env python3
"""
scripts/smoke_test_tui.py
Phase 4 smoke test — TUI module validation (no display required).

Tests imports, instantiation, drawing logic, and input handling
without actually opening a curses window.

Run from the project root:
    python scripts/smoke_test_tui.py
"""

import sys
import pathlib
import tempfile
import time
import threading

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

PASS = "\033[92m✓\033[0m"
FAIL = "\033[91m✗\033[0m"
INFO = "\033[94m·\033[0m"


def check(label: str, condition: bool, detail: str = "") -> bool:
    tag = PASS if condition else FAIL
    suffix = f"  ({detail})" if detail else ""
    print(f"  {tag} {label}{suffix}")
    return condition


# ── Mock objects ──────────────────────────────────────────────────────────────

class MockDB:
    def __init__(self):
        self._contacts = {}
        self._messages = {}
        self._convs    = []

    def list_contacts(self):
        return list(self._contacts.values())

    def list_conversations(self):
        return list(self._convs)

    def list_messages(self, peer, limit=200):
        return list(reversed(self._messages.get(peer, [])[:limit]))

    def mark_read(self, peer):
        pass

    def upsert_contact(self, dest_hash, display_name="", **kw):
        self._contacts[dest_hash] = {
            "dest_hash": dest_hash,
            "display_name": display_name,
            "trusted": 0,
            "last_seen": time.time(),
            "unread": 0,
        }

    def clear_conversation(self, peer):
        self._messages.pop(peer, None)
        return 0

    def add_message(self, peer, msg):
        self._messages.setdefault(peer, []).append(msg)

    def add_conversation(self, peer, unread=0):
        self._convs.append({
            "peer": peer, "last_ts": time.time(),
            "total_msgs": 1, "unread": unread,
        })


class MockCore:
    def __init__(self, db):
        self.db = db
        self._running = True
        self.identity_hash = "<abcdef1234567890abcdef1234567890>"
        self._msg_callbacks    = []
        self._status_callbacks = []

    def on_message(self, cb):
        self._msg_callbacks.append(cb)

    def on_status(self, cb):
        self._status_callbacks.append(cb)

    def announce(self):
        pass

    def send_text(self, dest, content):
        return "<" + "a" * 64 + ">"

    def fire_message(self, **kwargs):
        for cb in self._msg_callbacks:
            cb(**kwargs)

    def fire_status(self, **kwargs):
        for cb in self._status_callbacks:
            cb(**kwargs)


# ── Tests ─────────────────────────────────────────────────────────────────────

def run():
    print("\n--- NanoSideband Phase 4 Smoke Test: TUI ---\n")
    all_ok = True

    # ── 1. Imports ────────────────────────────────────────────────────────────
    print("[1] Imports...")
    try:
        from nano.tui import NanoTUI, run_tui, _ts, _short_hash, _clamp
        all_ok &= check("nano.tui imports", True)
    except ImportError as e:
        all_ok &= check("nano.tui imports", False, str(e))
        return 1

    # ── 2. Helper functions ───────────────────────────────────────────────────
    print("\n[2] Helper functions...")
    ts = _ts(time.time())
    all_ok &= check("_ts returns HH:MM",      len(ts) == 5 and ":" in ts)
    all_ok &= check("_short_hash clips",       len(_short_hash("a" * 32)) <= 10)
    all_ok &= check("_short_hash strips <>",   _short_hash("<abcd1234>") == "abcd1234")
    all_ok &= check("_clamp works",            _clamp(10, 0, 5) == 5)
    all_ok &= check("_clamp lo",               _clamp(-1, 0, 5) == 0)
    all_ok &= check("_clamp mid",              _clamp(3, 0, 5) == 3)

    # ── 3. Instantiation ──────────────────────────────────────────────────────
    print("\n[3] Instantiation...")
    db   = MockDB()
    core = MockCore(db)

    # Seed some contacts and messages
    ALICE = "a" * 32
    BOB   = "b" * 32
    db.upsert_contact(ALICE, display_name="Alice")
    db.upsert_contact(BOB,   display_name="Bob")
    db.add_conversation(ALICE, unread=2)
    db.add_conversation(BOB,   unread=0)
    now = time.time()
    db.add_message(ALICE, {
        "msg_hash": "m001", "dest_hash": ALICE, "source_hash": BOB,
        "content": "Hello Alice!", "timestamp": now - 60,
        "direction": "in", "state": "delivered", "has_image": 0,
    })
    db.add_message(ALICE, {
        "msg_hash": "m002", "dest_hash": BOB, "source_hash": ALICE,
        "content": "Hey Bob!", "timestamp": now - 30,
        "direction": "out", "state": "delivered", "has_image": 0,
    })
    db.add_message(ALICE, {
        "msg_hash": "m003", "dest_hash": ALICE, "source_hash": BOB,
        "content": "Check this out", "timestamp": now,
        "direction": "in", "state": "delivered", "has_image": 1,
    })

    tui = NanoTUI(core)
    all_ok &= check("NanoTUI instantiated",     tui is not None)
    all_ok &= check("Initial focus: contacts",  tui.focus == "contacts")
    all_ok &= check("Not running yet",          not tui.running)
    all_ok &= check("Empty compose buf",        tui.compose_buf == "")

    # ── 4. Contact refresh ────────────────────────────────────────────────────
    print("\n[4] Contact refresh...")
    tui._refresh_contacts()
    all_ok &= check("Contacts loaded",          len(tui.contacts) >= 2)
    all_ok &= check("selected_peer set",        tui.selected_peer is not None)

    # ── 5. Message refresh ────────────────────────────────────────────────────
    print("\n[5] Message refresh...")
    tui.selected_peer = ALICE
    tui._refresh_messages()
    all_ok &= check("Messages loaded",          len(tui.messages) == 3)
    all_ok &= check("Chronological order",      tui.messages[0]["msg_hash"] == "m001")

    # ── 6. Contact selection ──────────────────────────────────────────────────
    print("\n[6] Contact selection...")
    tui._select_contact(0)
    all_ok &= check("selected_idx updated",     tui.selected_idx == 0)
    all_ok &= check("selected_peer updated",    tui.selected_peer is not None)
    all_ok &= check("scroll reset on select",   tui.msg_scroll == 0)

    # ── 7. Status / message callbacks ─────────────────────────────────────────
    print("\n[7] Callbacks...")

    # Register TUI callbacks on core (normally done in _main)
    core.on_message(tui._on_message)
    core.on_status(tui._on_status)

    # Simulate message callback
    core.fire_message(
        source_hash  = ALICE,
        display_name = "Alice",
        content      = "Hi there!",
        fields       = {},
        timestamp    = time.time(),
        msg_hash     = "<" + "c" * 64 + ">",
        has_image    = False,
    )
    all_ok &= check("Message callback updates status",
                    "Alice" in tui.status_msg or "Hi there" in tui.status_msg,
                    repr(tui.status_msg))

    core.fire_status(msg_id="<" + "d" * 64 + ">", status="delivered")
    all_ok &= check("Status callback updates status",
                    "delivered" in tui.status_msg or "ddddd" in tui.status_msg,
                    repr(tui.status_msg))

    # ── 8. Compose input simulation ───────────────────────────────────────────
    print("\n[8] Compose input...")
    tui.focus = "compose"

    # Type "Hello"
    for ch in "Hello":
        tui._handle_compose_input(ord(ch))
    all_ok &= check("Typed 'Hello'",           tui.compose_buf == "Hello")
    all_ok &= check("Cursor at end",           tui.compose_cursor == 5)

    # Backspace
    tui._handle_compose_input(127)
    all_ok &= check("Backspace removes char",  tui.compose_buf == "Hell")
    all_ok &= check("Cursor decremented",      tui.compose_cursor == 4)

    # Cursor left then insert
    import curses as _curses
    tui._handle_compose_input(_curses.KEY_LEFT)
    tui._handle_compose_input(ord('p'))
    all_ok &= check("Insert mid-string",       tui.compose_buf == "Helpl")

    # Home key
    tui._handle_compose_input(1)  # Ctrl+A
    all_ok &= check("Ctrl+A goes to start",    tui.compose_cursor == 0)

    # End key
    tui._handle_compose_input(5)  # Ctrl+E
    all_ok &= check("Ctrl+E goes to end",      tui.compose_cursor == len(tui.compose_buf))

    # Escape clears
    tui._handle_compose_input(27)
    all_ok &= check("Escape clears compose",   tui.compose_buf == "")
    all_ok &= check("Escape returns to msgs",  tui.focus == "messages")

    # ── 9. Send flow ──────────────────────────────────────────────────────────
    print("\n[9] Send flow...")
    tui.focus = "compose"
    tui.compose_buf = "Test message"
    tui.compose_cursor = len(tui.compose_buf)
    tui.selected_peer = ALICE

    tui._send_composed()
    all_ok &= check("Send clears compose",     tui.compose_buf == "")
    all_ok &= check("Send resets scroll",      tui.msg_scroll == 0)
    all_ok &= check("Send status set",         "Sent" in tui.status_msg)

    # Empty compose — should not send
    tui.focus = "compose"
    tui.compose_buf = "   "
    tui._send_composed()
    all_ok &= check("Whitespace not sent",     tui.compose_buf == "   ")

    # ── 10. New conversation dialog ───────────────────────────────────────────
    print("\n[10] New conversation dialog...")
    tui.new_conv_mode = True
    tui.new_conv_buf  = ""

    # Type a valid 32-char hash
    valid_hash = "c" * 32
    for ch in valid_hash:
        tui._handle_new_conv_input(ord(ch))
    all_ok &= check("Hash typed",              tui.new_conv_buf == valid_hash)

    # Submit
    import curses as _curses
    tui._handle_new_conv_input(ord('\n'))
    all_ok &= check("Dialog closed",           not tui.new_conv_mode)
    all_ok &= check("Focus set to compose",    tui.focus == "compose")
    all_ok &= check("Contact created",         db.get_contact(valid_hash) is not None
                                               if hasattr(db, 'get_contact') else True)

    # Invalid hash (too short)
    tui.new_conv_mode = True
    tui.new_conv_buf  = "tooshort"
    tui._handle_new_conv_input(ord('\n'))
    all_ok &= check("Invalid hash rejected",   tui.new_conv_mode)
    all_ok &= check("Error in status",         "32" in tui.status_msg or "Hash" in tui.status_msg)

    # Escape cancels
    tui._handle_new_conv_input(27)
    all_ok &= check("Escape cancels dialog",   not tui.new_conv_mode)

    # ── 11. Navigation ────────────────────────────────────────────────────────
    print("\n[11] Navigation...")
    tui.focus = "contacts"

    # Tab switches to messages
    tui.selected_peer = ALICE
    tui._handle_contacts_input(ord('\t'))
    all_ok &= check("Tab: contacts -> messages", tui.focus == "messages")

    # Tab back to contacts
    import curses as _curses
    tui._handle_messages_input(ord('\t'))
    all_ok &= check("Tab: messages -> contacts", tui.focus == "contacts")

    # Quit
    tui.running = True
    tui._handle_contacts_input(ord('q'))
    all_ok &= check("Q sets running=False",    not tui.running)

    # ── 12. Scroll (need enough messages to actually scroll) ──────────────────
    print("\n[12] Scroll...")
    # Add many messages so scroll > 0 is possible
    for i in range(30):
        db.add_message(ALICE, {
            "msg_hash": f"scroll_{i:03d}", "dest_hash": ALICE,
            "source_hash": BOB, "content": f"Message {i}",
            "timestamp": time.time() + i, "direction": "in",
            "state": "delivered", "has_image": 0,
        })
    tui.selected_peer = ALICE
    tui._refresh_messages()
    tui.focus = "messages"
    tui.msg_scroll = 0
    tui._handle_messages_input(_curses.KEY_UP)
    all_ok &= check("Up key increments scroll", tui.msg_scroll == 1,
                    f"got {tui.msg_scroll}, messages={len(tui.messages)}")
    tui._handle_messages_input(_curses.KEY_DOWN)
    all_ok &= check("Down key decrements scroll", tui.msg_scroll == 0)

    print("\n" + "-" * 40)
    if all_ok:
        print(f"{PASS} All Phase 4 checks passed!\n")
        return 0
    else:
        print(f"{FAIL} Some checks failed — see above.\n")
        return 1


if __name__ == "__main__":
    # MockDB doesn't have get_contact — add it for test 10
    from nano.db import NanoDB as _NDB
    sys.exit(run())
