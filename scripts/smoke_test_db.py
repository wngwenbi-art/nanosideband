#!/usr/bin/env python3
"""
scripts/smoke_test_db.py
Phase 2 smoke test — database layer.

Run from the project root:
    python scripts/smoke_test_db.py
"""

import sys
import pathlib
import tempfile
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
    print("\n--- NanoSideband Phase 2 Smoke Test: Database ---\n")
    all_ok = True

    from nano.db import NanoDB

    with tempfile.TemporaryDirectory() as tmp:
        db_path = pathlib.Path(tmp) / "test.db"

        # ── 1. Open / close ───────────────────────────────────────────────────
        print("[1] Open / close...")
        with NanoDB(db_path) as db:
            all_ok &= check("DB opened", db._conn is not None)
            s = db.stats()
            all_ok &= check("Empty stats", s["contacts"] == 0 and s["messages"] == 0)
        all_ok &= check("DB closed", db._conn is None)

        # ── 2. Contacts ───────────────────────────────────────────────────────
        print("\n[2] Contacts...")
        with NanoDB(db_path) as db:
            ALICE = "a" * 32
            BOB   = "b" * 32

            db.upsert_contact(ALICE, display_name="Alice", trusted=True)
            db.upsert_contact(BOB,   display_name="Bob",   trusted=False)

            all_ok &= check("Contact count", db.stats()["contacts"] == 2)

            alice = db.get_contact(ALICE)
            all_ok &= check("Alice retrieved",    alice is not None)
            all_ok &= check("Alice name correct", alice["display_name"] == "Alice")
            all_ok &= check("Alice trusted",      alice["trusted"] == 1)

            bob = db.get_contact(BOB)
            all_ok &= check("Bob not trusted",    bob["trusted"] == 0)

            db.set_trusted(BOB, True)
            all_ok &= check("Bob now trusted",    db.get_contact(BOB)["trusted"] == 1)

            trusted = db.list_contacts(trusted_only=True)
            all_ok &= check("Two trusted contacts", len(trusted) == 2)

            # upsert updates existing
            db.upsert_contact(ALICE, display_name="Alice Updated")
            all_ok &= check("Upsert updates name",
                            db.get_contact(ALICE)["display_name"] == "Alice Updated")

            db.touch_contact(ALICE, display_name="Alice Touched")
            all_ok &= check("Touch updates name",
                            db.get_contact(ALICE)["display_name"] == "Alice Touched")

            # touch creates if missing
            CHARLIE = "c" * 32
            db.touch_contact(CHARLIE, display_name="Charlie")
            all_ok &= check("Touch creates new contact",
                            db.get_contact(CHARLIE) is not None)

            db.delete_contact(CHARLIE)
            all_ok &= check("Delete contact",
                            db.get_contact(CHARLIE) is None)

        # ── 3. Messages ───────────────────────────────────────────────────────
        print("\n[3] Messages...")
        with NanoDB(db_path) as db:
            ALICE = "a" * 32
            BOB   = "b" * 32

            now = time.time()
            # Inbound: Bob sends to Alice
            db.save_message(
                msg_hash    = "msg001",
                dest_hash   = ALICE,
                source_hash = BOB,
                content     = "Hello Alice!",
                timestamp   = now - 10,
                direction   = "in",
                state       = "pending",
                rx_ts       = now - 10,
            )
            # Outbound: Alice replies to Bob
            db.save_message(
                msg_hash    = "msg002",
                dest_hash   = BOB,
                source_hash = ALICE,
                content     = "Hello Bob!",
                timestamp   = now - 5,
                direction   = "out",
                state       = "pending",
                tx_ts       = now - 5,
            )
            # Inbound with image
            db.save_message(
                msg_hash    = "msg003",
                dest_hash   = ALICE,
                source_hash = BOB,
                content     = "Check this pic",
                timestamp   = now,
                direction   = "in",
                state       = "pending",
                has_image   = True,
                rx_ts       = now,
            )

            all_ok &= check("3 messages saved", db.stats()["messages"] == 3)
            all_ok &= check("2 unread",         db.stats()["unread"] == 2)

            # Duplicate prevention
            dup = db.save_message(
                msg_hash="msg001", dest_hash=ALICE, source_hash=BOB,
                content="dup", timestamp=now, direction="in", state="pending"
            )
            all_ok &= check("Duplicate rejected", dup == False)

            # Retrieve single message
            m = db.get_message("msg001")
            all_ok &= check("Get message",       m is not None)
            all_ok &= check("Content correct",   m["content"] == "Hello Alice!")
            all_ok &= check("Direction correct", m["direction"] == "in")

            # List conversation
            msgs = db.list_messages(BOB)
            all_ok &= check("List conv with Bob (3 msgs)", len(msgs) == 3)

            # Pagination
            msgs_page = db.list_messages(BOB, limit=2)
            all_ok &= check("Pagination limit=2", len(msgs_page) == 2)

            # State update
            db.update_message_state("msg002", "delivered")
            all_ok &= check("State updated",
                            db.get_message("msg002")["state"] == "delivered")

            # Mark read
            db.mark_read(BOB)
            all_ok &= check("Mark read clears unread", db.stats()["unread"] == 0)

            # Message count
            all_ok &= check("message_count(BOB)", db.message_count(BOB) == 3)

            # Conversations list
            convs = db.list_conversations()
            peers = {c["peer"] for c in convs}
            all_ok &= check("Conversations includes Alice and Bob",
                            ALICE in peers and BOB in peers)

        # ── 4. Images ─────────────────────────────────────────────────────────
        print("\n[4] Images...")
        with NanoDB(db_path) as db:
            fake_jpeg = b"\xff\xd8\xff" + b"\x00" * 100  # fake JPEG header
            db.save_image("msg003", fake_jpeg, mime_type="image/jpeg",
                          width=640, height=480)

            all_ok &= check("Image count",     db.stats()["images"] == 1)
            all_ok &= check("image_exists",    db.image_exists("msg003"))
            all_ok &= check("No image for 001", not db.image_exists("msg001"))

            img = db.get_image("msg003")
            all_ok &= check("Image retrieved",     img is not None)
            all_ok &= check("Image data correct",  img["image_data"] == fake_jpeg)
            all_ok &= check("Image dimensions",
                            img["width"] == 640 and img["height"] == 480)

        # ── 5. Housekeeping ───────────────────────────────────────────────────
        print("\n[5] Housekeeping...")
        with NanoDB(db_path) as db:
            # Clear conversation
            ALICE = "a" * 32
            BOB   = "b" * 32
            before = db.message_count(BOB)
            deleted = db.clear_conversation(BOB)
            all_ok &= check("Clear conversation",
                            deleted == before and db.message_count(BOB) == 0)

            # Purge old — insert an ancient message
            db.save_message(
                msg_hash    = "ancient",
                dest_hash   = ALICE,
                source_hash = BOB,
                content     = "old",
                timestamp   = time.time() - 91 * 86400,
                direction   = "in",
                state       = "delivered",
            )
            purged = db.purge_old_messages(older_than_days=90)
            all_ok &= check("Purge old messages", purged == 1)

        # ── 6. Persistence across open/close ──────────────────────────────────
        print("\n[6] Persistence...")
        ALICE = "a" * 32
        BOB   = "b" * 32
        with NanoDB(db_path) as db:
            db.upsert_contact("persist01", display_name="Persistent Pete")
            db.save_message(
                msg_hash="persist_msg", dest_hash=ALICE, source_hash=BOB,
                content="still here?", timestamp=time.time(),
                direction="in", state="pending"
            )

        with NanoDB(db_path) as db:
            c = db.get_contact("persist01")
            m = db.get_message("persist_msg")
            all_ok &= check("Contact persisted across close/open", c is not None)
            all_ok &= check("Message persisted across close/open", m is not None)
            all_ok &= check("Content intact", m["content"] == "still here?")

    print("\n" + "-" * 40)
    if all_ok:
        print(f"{PASS} All Phase 2 checks passed!\n")
        return 0
    else:
        print(f"{FAIL} Some checks failed - see above.\n")
        return 1


if __name__ == "__main__":
    sys.exit(run())
