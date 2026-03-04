#!/usr/bin/env python3
"""
scripts/smoke_test_web.py
Phase 5 smoke test — web UI (Flask test client, no browser needed).

Run from the project root:
    python scripts/smoke_test_web.py
"""

import sys
import pathlib
import time
import json
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

PASS = "\033[92m✓\033[0m"
FAIL = "\033[91m✗\033[0m"
INFO = "\033[94m·\033[0m"


def check(label: str, condition: bool, detail: str = "") -> bool:
    tag = PASS if condition else FAIL
    suffix = f"  ({detail})" if detail else ""
    print(f"  {tag} {label}{suffix}")
    return condition


# ── Mock objects (same pattern as TUI test) ───────────────────────────────────

class MockDB:
    def __init__(self):
        self._contacts = {}
        self._messages = {}
        self._images   = {}

    def list_contacts(self):
        return list(self._contacts.values())

    def list_conversations(self):
        peers = set()
        for peer, msgs in self._messages.items():
            if msgs:
                peers.add(peer)
        result = []
        for peer in peers:
            msgs = self._messages[peer]
            unread = sum(1 for m in msgs if m["direction"]=="in" and m["state"]=="pending")
            result.append({
                "peer": peer,
                "last_ts": max(m["timestamp"] for m in msgs),
                "total_msgs": len(msgs),
                "unread": unread,
            })
        return result

    def list_messages(self, peer, limit=50, before_ts=None):
        msgs = self._messages.get(peer, [])
        if before_ts:
            msgs = [m for m in msgs if m["timestamp"] < before_ts]
        return list(reversed(msgs[-limit:]))

    def get_contact(self, dest_hash):
        return self._contacts.get(dest_hash)

    def upsert_contact(self, dest_hash, display_name="", **kw):
        self._contacts[dest_hash] = {
            "dest_hash": dest_hash,
            "display_name": display_name,
            "trusted": 0,
            "last_seen": time.time(),
        }

    def mark_read(self, peer):
        for m in self._messages.get(peer, []):
            if m["direction"] == "in":
                m["state"] = "delivered"

    def clear_conversation(self, peer):
        self._messages.pop(peer, None)
        return 0

    def stats(self):
        return {
            "contacts": len(self._contacts),
            "messages": sum(len(v) for v in self._messages.values()),
            "images":   len(self._images),
            "unread":   0,
            "db_path":  ":memory:",
        }

    def save_message(self, msg_hash, dest_hash, source_hash, content,
                     timestamp, direction, state="pending", has_image=False,
                     rx_ts=None, tx_ts=None):
        # Store under the PEER's hash (the other side of the conversation)
        my_hash = "abcdef1234567890abcdef1234567890"
        if direction == "in":
            peer = source_hash
        else:
            peer = dest_hash
        msg = {
            "msg_hash": msg_hash, "dest_hash": dest_hash,
            "source_hash": source_hash, "content": content,
            "timestamp": timestamp, "direction": direction,
            "state": state, "has_image": int(has_image),
        }
        self._messages.setdefault(peer, []).append(msg)
        return True

    def get_image(self, msg_hash):
        return self._images.get(msg_hash)

    def save_image(self, msg_hash, data, mime_type="image/jpeg", width=0, height=0):
        self._images[msg_hash] = {
            "msg_hash": msg_hash, "image_data": data,
            "mime_type": mime_type, "width": width, "height": height,
        }

    def update_message_state(self, msg_hash, state):
        for msgs in self._messages.values():
            for m in msgs:
                if m["msg_hash"] == msg_hash:
                    m["state"] = state


class MockCore:
    def __init__(self, db):
        self.db = db
        self.config = {"display_name": "Test User", "web_port": 5000}
        self._running = True
        self.identity_hash = "<abcdef1234567890abcdef1234567890>"
        self._msg_cbs    = []
        self._status_cbs = []

    def on_message(self, cb):         self._msg_cbs.append(cb)
    def on_delivery_status(self, cb): self._status_cbs.append(cb)
    def announce(self):               pass

    def send_text(self, dest, content):
        h = "a" * 64
        self.db.save_message(h, dest, "abcdef1234567890abcdef1234567890",
                             content, time.time(), "out", "pending")
        return "<" + h + ">"

    def send_image(self, dest, img_data, caption=""):
        h = "b" * 64
        self.db.save_message(h, dest, "abcdef1234567890abcdef1234567890",
                             caption, time.time(), "out", "pending", has_image=True)
        return "<" + h + ">"


def run():
    print("\n--- NanoSideband Phase 5 Smoke Test: Web UI ---\n")
    all_ok = True

    # ── 0. Flask available? ───────────────────────────────────────────────────
    print("[0] Checking Flask...")
    try:
        import flask
        all_ok &= check("Flask available", True, flask.__version__)
    except ImportError:
        all_ok &= check("Flask available", False, "run: pip install flask")
        print("\n  Flask required. Skipping remaining tests.\n")
        return 1

    from nano.webui import create_app
    all_ok &= check("nano.webui imports", True)

    # ── 1. App creation ───────────────────────────────────────────────────────
    print("\n[1] App creation...")
    db   = MockDB()
    core = MockCore(db)

    # Seed data
    ALICE = "a" * 32
    BOB   = "b" * 32
    now   = time.time()

    db.upsert_contact(ALICE, display_name="Alice")
    db.upsert_contact(BOB,   display_name="Bob")
    db.save_message("m001", ALICE, BOB, "Hello Alice!", now-60, "in", "delivered")
    db.save_message("m002", BOB, ALICE, "Hey Bob!",    now-30, "out", "pending")
    db.save_message("m003", ALICE, BOB, "Got an image", now, "in", "delivered",
                    has_image=True)
    db.save_image("m003", b"\xff\xd8\xff" + b"\x00"*50, "image/jpeg", 100, 80)

    app = create_app(core, db)
    all_ok &= check("Flask app created", app is not None)

    client = app.test_client()
    all_ok &= check("Test client created", client is not None)

    # ── 2. Basic routes ───────────────────────────────────────────────────────
    print("\n[2] Basic routes...")

    r = client.get("/")
    all_ok &= check("GET / redirects",     r.status_code in (301, 302))

    r = client.get("/conversations")
    all_ok &= check("GET /conversations",  r.status_code == 200)
    all_ok &= check("Contains Alice",      b"Alice" in r.data)
    all_ok &= check("Contains Bob",        b"Bob" in r.data)
    all_ok &= check("Contains NanoSideband", b"NanoSideband" in r.data)

    r = client.get(f"/conversations/{BOB}")
    all_ok &= check("GET /conversations/bob",    r.status_code == 200)
    all_ok &= check("Shows inbound message",     b"Hello Alice" in r.data)
    all_ok &= check("Shows outbound message",    b"Hey Bob" in r.data)
    all_ok &= check("Image placeholder present",
                    b"image" in r.data.lower() or b"/image/" in r.data)

    r = client.get("/whoami")
    all_ok &= check("GET /whoami",         r.status_code == 200)
    all_ok &= check("Shows identity hash", b"abcdef" in r.data)
    all_ok &= check("Shows display name",  b"Test User" in r.data)

    # ── 3. Send API ───────────────────────────────────────────────────────────
    print("\n[3] Send API...")

    r = client.post(f"/send/{ALICE}",
                    data=json.dumps({"content": "Hello from web!"}),
                    content_type="application/json")
    all_ok &= check("POST /send returns 200",   r.status_code == 200)
    data = json.loads(r.data)
    all_ok &= check("Returns msg_hash",         "msg_hash" in data, str(data))

    # Empty content rejected
    r = client.post(f"/send/{ALICE}",
                    data=json.dumps({"content": ""}),
                    content_type="application/json")
    all_ok &= check("Empty content rejected",   r.status_code == 400)

    # ── 4. Image send ─────────────────────────────────────────────────────────
    print("\n[4] Image send...")
    # Create a real minimal JPEG using Pillow if available
    try:
        from PIL import Image as PILImage
        import io
        buf = io.BytesIO()
        PILImage.new("RGB", (10, 10), (255, 0, 0)).save(buf, format="JPEG")
        real_jpeg = buf.getvalue()
    except ImportError:
        real_jpeg = None

    if real_jpeg:
        import io as _io
        r = client.post(
            f"/send_image/{ALICE}",
            data={"image": (_io.BytesIO(real_jpeg), "test.jpg"), "caption": "Nice pic"},
            content_type="multipart/form-data",
        )
        all_ok &= check("POST /send_image responds 200",
                        r.status_code == 200,
                        f"status={r.status_code} body={r.data[:80]}")
    else:
        print(f"  {INFO} Pillow not available, skipping image send test")

    # ── 5. New conversation ───────────────────────────────────────────────────
    print("\n[5] New conversation...")
    NEW_HASH = "c" * 32
    r = client.post("/new",
                    data=json.dumps({"hash": NEW_HASH}),
                    content_type="application/json")
    all_ok &= check("POST /new returns 200",    r.status_code == 200)
    all_ok &= check("Contact created",          db.get_contact(NEW_HASH) is not None)

    # ── 6. Announce ───────────────────────────────────────────────────────────
    print("\n[6] Announce...")
    r = client.post("/announce")
    all_ok &= check("POST /announce returns 200", r.status_code == 200)

    # ── 7. Image serving ─────────────────────────────────────────────────────
    print("\n[7] Image serving...")
    r = client.get("/image/m003")
    all_ok &= check("GET /image/m003 returns 200",    r.status_code == 200)
    all_ok &= check("Content-Type is image",
                    "image" in r.content_type, r.content_type)
    all_ok &= check("Returns image bytes",            len(r.data) > 0)

    r = client.get("/image/nonexistent")
    all_ok &= check("Missing image returns 404",      r.status_code == 404)

    # ── 8. JSON API ───────────────────────────────────────────────────────────
    print("\n[8] JSON API...")
    r = client.get("/api/conversations")
    all_ok &= check("GET /api/conversations 200",     r.status_code == 200)
    convs = json.loads(r.data)
    all_ok &= check("Returns list",                   isinstance(convs, list))
    hashes = [c["dest_hash"] for c in convs]
    all_ok &= check("Contains Alice",                 ALICE in hashes, str(hashes))

    r = client.get(f"/api/messages/{BOB}")
    all_ok &= check("GET /api/messages 200",          r.status_code == 200)
    msgs = json.loads(r.data)
    all_ok &= check("Returns message list",           isinstance(msgs, list))
    all_ok &= check("Contains seeded message",
                    any(m.get("content") == "Hey Bob!" for m in msgs),
                    str([m.get("content") for m in msgs]))

    r = client.get("/api/status")
    all_ok &= check("GET /api/status 200",            r.status_code == 200)
    status = json.loads(r.data)
    all_ok &= check("Status has running field",       "running" in status)
    all_ok &= check("Status has identity_hash",       "identity_hash" in status)
    all_ok &= check("Status shows running=True",      status["running"] is True)

    # ── 9. Delete conversation ────────────────────────────────────────────────
    print("\n[9] Delete...")
    r = client.get(f"/conversations/{ALICE}/delete")
    all_ok &= check("DELETE redirects",               r.status_code in (301,302))
    all_ok &= check("Messages cleared",               len(db._messages.get(ALICE,[])) == 0)

    # ── 10. HTML structure checks ─────────────────────────────────────────────
    print("\n[10] HTML structure...")
    db.save_message("m010", BOB, "abcdef1234567890abcdef1234567890", "fresh msg", time.time(), "out", "pending")
    r = client.get(f"/conversations/{BOB}")
    html = r.data.decode("utf-8", errors="replace")
    all_ok &= check("Has compose input",
                    'id="compose-input"' in html)
    all_ok &= check("Has send button",
                    ">Send<" in html)
    all_ok &= check("Has image upload",
                    'type="file"' in html)
    all_ok &= check("Has SSE script",
                    "EventSource" in html)
    all_ok &= check("Has mobile viewport meta",
                    'viewport' in html)
    all_ok &= check("Has dark theme CSS vars",
                    '--bg:' in html)

    print("\n" + "-" * 40)
    if all_ok:
        print(f"{PASS} All Phase 5 checks passed!\n")
        return 0
    else:
        print(f"{FAIL} Some checks failed — see above.\n")
        return 1


if __name__ == "__main__":
    sys.exit(run())
