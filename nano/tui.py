"""
nano/tui.py
Curses-based terminal UI for NanoSideband.

Layout
------
┌──────────────────────────────────────────────────────────┐
│ NanoSideband  <identity_hash>              [●] Connected │  header
├────────────────┬─────────────────────────────────────────┤
│ Contacts   [C] │ Bob <hash>                              │  pane titles
├────────────────┼─────────────────────────────────────────┤
│ > Bob       2  │  11:23  Bob: Hello!                     │
│   Alice        │  11:24  You: Hey Bob                    │  message
│   Charlie      │  11:25  Bob: How are you? [img]         │  area
│                │                                         │
│                │                                         │
├────────────────┴─────────────────────────────────────────┤
│ > _                                                      │  compose
├──────────────────────────────────────────────────────────┤
│ Tab:switch  Enter:send  A:announce  N:new  D:delete  Q:quit│  help
└──────────────────────────────────────────────────────────┘

Key bindings
------------
Tab         — switch focus between contact list and message area
↑ / ↓       — navigate contacts or scroll messages
Enter       — select contact (list) / send message (compose)
A           — send announce
N           — new conversation (enter hash)
D           — delete selected conversation
/           — focus compose bar
Esc         — cancel compose / back to list
Q           — quit

Windows note: requires `pip install windows-curses`
"""

import curses
import threading
import time
import textwrap
import logging
from datetime import datetime
from typing import Optional

log = logging.getLogger(__name__)

# ── Colour pair indices ───────────────────────────────────────────────────────
C_NORMAL    = 0
C_HEADER    = 1
C_SELECTED  = 2
C_UNREAD    = 3
C_TIMESTAMP = 4
C_SELF      = 5
C_STATUS_OK = 6
C_STATUS_ERR= 7
C_DIVIDER   = 8
C_HELP      = 9
C_INPUT     = 10


def _init_colours():
    curses.start_color()
    curses.use_default_colors()
    bg = -1  # transparent background
    curses.init_pair(C_HEADER,    curses.COLOR_BLACK,  curses.COLOR_CYAN)
    curses.init_pair(C_SELECTED,  curses.COLOR_BLACK,  curses.COLOR_WHITE)
    curses.init_pair(C_UNREAD,    curses.COLOR_YELLOW, bg)
    curses.init_pair(C_TIMESTAMP, curses.COLOR_CYAN,   bg)
    curses.init_pair(C_SELF,      curses.COLOR_GREEN,  bg)
    curses.init_pair(C_STATUS_OK, curses.COLOR_GREEN,  bg)
    curses.init_pair(C_STATUS_ERR,curses.COLOR_RED,    bg)
    curses.init_pair(C_DIVIDER,   curses.COLOR_CYAN,   bg)
    curses.init_pair(C_HELP,      curses.COLOR_BLACK,  curses.COLOR_WHITE)
    curses.init_pair(C_INPUT,     curses.COLOR_WHITE,  curses.COLOR_BLUE)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ts(ts: float) -> str:
    """Format a unix timestamp as HH:MM."""
    return datetime.fromtimestamp(ts).strftime("%H:%M")


def _short_hash(h: str) -> str:
    """Show first 8 chars of a hash."""
    h = h.strip("<>")
    return h[:8] + "…" if len(h) > 8 else h


def _clamp(value, lo, hi):
    return max(lo, min(hi, value))


def _addstr_clipped(win, y, x, text, attr=0):
    """Write text clipped to window width — never raises curses errors."""
    try:
        max_y, max_x = win.getmaxyx()
        if y < 0 or y >= max_y or x >= max_x:
            return
        available = max_x - x - 1
        if available <= 0:
            return
        win.addstr(y, x, text[:available], attr)
    except curses.error:
        pass


# ── Main TUI class ────────────────────────────────────────────────────────────

class NanoTUI:
    """
    Full-screen curses TUI for NanoSideband.

    Usage:
        tui = NanoTUI(core)
        tui.run()   # blocks until user quits
    """

    CONTACT_WIDTH = 20   # width of left pane (contacts)
    HELP_HEIGHT   = 1    # height of bottom help bar
    COMPOSE_HEIGHT= 1    # height of compose area
    HEADER_HEIGHT = 1

    def __init__(self, core):
        self.core    = core
        self.db      = core.db
        self._lock   = threading.Lock()

        # State
        self.contacts: list[dict] = []      # from db.list_contacts()
        self.selected_idx   = 0             # selected contact index
        self.selected_peer  = None          # hash of selected peer
        self.messages: list[dict] = []      # messages for selected conv
        self.msg_scroll     = 0             # scroll offset (lines from bottom)

        self.focus          = "contacts"    # "contacts" | "messages" | "compose"
        self.compose_buf    = ""            # text being typed
        self.compose_cursor = 0
        self.status_msg     = ""            # bottom status flash
        self.status_ts      = 0.0
        self.running        = False

        # New conversation dialog state
        self.new_conv_mode  = False
        self.new_conv_buf   = ""

        # Incoming message notification
        self._new_msg_peer  = None          # peer hash of latest new message

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def run(self):
        """Start the TUI. Blocks until user quits."""
        curses.wrapper(self._main)

    def _main(self, stdscr):
        self.stdscr = stdscr
        self.running = True

        curses.curs_set(0)
        stdscr.nodelay(True)
        stdscr.keypad(True)

        _init_colours()

        # Register message callback on core
        self.core.on_message(self._on_message)
        self.core.on_status(self._on_status)

        self._refresh_contacts()

        # Announce on startup
        self.core.announce()
        self._set_status("Announced — waiting for peers…")

        last_refresh = 0.0

        while self.running:
            now = time.time()

            # Periodic refresh every 2 seconds
            if now - last_refresh > 2.0:
                self._refresh_contacts()
                if self.selected_peer:
                    self._refresh_messages()
                last_refresh = now

            self._draw()
            self._handle_input()
            time.sleep(0.05)

    # ── Data refresh ──────────────────────────────────────────────────────────

    def _refresh_contacts(self):
        """Pull latest contact + unread counts from DB."""
        try:
            convs   = {c["peer"]: c for c in self.db.list_conversations()}
            contacts = self.db.list_contacts()

            # Merge: contacts in DB + any peers who messaged us but aren't contacts
            known = {c["dest_hash"] for c in contacts}
            for peer, conv in convs.items():
                if peer not in known:
                    contacts.append({
                        "dest_hash":    peer,
                        "display_name": _short_hash(peer),
                        "trusted":      0,
                        "last_seen":    conv["last_ts"],
                        "unread":       conv.get("unread", 0),
                    })

            # Annotate with unread counts
            for c in contacts:
                conv = convs.get(c["dest_hash"], {})
                c["unread"]   = conv.get("unread", 0)
                c["last_ts"]  = conv.get("last_ts", c.get("last_seen", 0))

            # Sort by last activity
            contacts.sort(key=lambda c: c.get("last_ts", 0), reverse=True)

            with self._lock:
                self.contacts = contacts

                # Keep selected_idx in bounds
                if self.contacts:
                    self.selected_idx = _clamp(
                        self.selected_idx, 0, len(self.contacts) - 1
                    )
                    if self.selected_peer is None:
                        self.selected_peer = self.contacts[0]["dest_hash"]

        except Exception as exc:
            log.debug("Contact refresh error: %s", exc)

    def _refresh_messages(self):
        """Pull latest messages for selected conversation."""
        if not self.selected_peer:
            return
        try:
            msgs = self.db.list_messages(self.selected_peer, limit=200)
            msgs.reverse()  # chronological order
            with self._lock:
                self.messages = msgs
        except Exception as exc:
            log.debug("Message refresh error: %s", exc)

    def _select_contact(self, idx):
        with self._lock:
            if not self.contacts:
                return
            self.selected_idx  = _clamp(idx, 0, len(self.contacts) - 1)
            self.selected_peer = self.contacts[self.selected_idx]["dest_hash"]
            self.msg_scroll    = 0
        self._refresh_messages()
        try:
            self.db.mark_read(self.selected_peer)
        except Exception:
            pass

    # ── Callbacks (called from core threads) ──────────────────────────────────

    def _on_message(self, source_hash, display_name, content,
                    fields, timestamp, msg_hash, has_image, **kw):
        """Called by NanoCore when a message arrives."""
        src = source_hash.strip("<>")
        self._new_msg_peer = src
        self._refresh_contacts()
        if self.selected_peer and self.selected_peer == src:
            self._refresh_messages()
            self.db.mark_read(src)
        name = display_name or _short_hash(src)
        img_note = " [img]" if has_image else ""
        self._set_status(f"← {name}: {content[:40]}{img_note}")

    def _on_status(self, msg_id, status):
        """Called by NanoCore when delivery status changes."""
        self._set_status(f"Delivery {_short_hash(msg_id)}: {status}")

    def _set_status(self, msg: str):
        self.status_msg = msg
        self.status_ts  = time.time()

    # ── Drawing ───────────────────────────────────────────────────────────────

    def _draw(self):
        h, w = self.stdscr.getmaxyx()
        if h < 8 or w < 30:
            self.stdscr.clear()
            _addstr_clipped(self.stdscr, 0, 0, "Terminal too small!", curses.A_BOLD)
            self.stdscr.refresh()
            return

        self.stdscr.erase()

        cw = self.CONTACT_WIDTH
        mw = w - cw - 1  # message pane width (−1 for divider)

        msg_area_h = h - self.HEADER_HEIGHT - self.COMPOSE_HEIGHT - self.HELP_HEIGHT - 2

        self._draw_header(w)
        self._draw_contacts(cw, msg_area_h)
        self._draw_divider(cw, h)
        self._draw_messages(cw + 1, mw, msg_area_h)
        self._draw_compose(w, h)
        self._draw_help(w, h)

        # Position cursor in compose if focused
        if self.focus == "compose" and not self.new_conv_mode:
            compose_y = h - self.HELP_HEIGHT - self.COMPOSE_HEIGHT - 1
            cx = min(2 + self.compose_cursor, w - 2)
            try:
                curses.curs_set(1)
                self.stdscr.move(compose_y, cx)
            except curses.error:
                pass
        else:
            try:
                curses.curs_set(0)
            except curses.error:
                pass

        self.stdscr.refresh()

    def _draw_header(self, w):
        identity = self.core.identity_hash or "<no identity>"
        connected = self.core._running
        status_char = "●" if connected else "○"
        status_color = curses.color_pair(C_STATUS_OK) if connected else curses.color_pair(C_STATUS_ERR)

        # Fill header bar
        header = f" NanoSideband  {identity}"
        self.stdscr.attron(curses.color_pair(C_HEADER))
        _addstr_clipped(self.stdscr, 0, 0, " " * w)
        _addstr_clipped(self.stdscr, 0, 0, header)
        self.stdscr.attroff(curses.color_pair(C_HEADER))

        # Status indicator at right
        try:
            self.stdscr.addstr(0, w - 14, f"[{status_char}]",
                               curses.color_pair(C_HEADER) | (curses.A_BOLD if connected else 0))
        except curses.error:
            pass

    def _draw_contacts(self, cw, list_h):
        y0 = self.HEADER_HEIGHT + 1  # +1 for pane title row

        # Pane title
        title = " Contacts"
        if self.focus == "contacts":
            title += " ◄"
        self.stdscr.attron(curses.color_pair(C_DIVIDER) | curses.A_BOLD)
        _addstr_clipped(self.stdscr, self.HEADER_HEIGHT, 0, title.ljust(cw))
        self.stdscr.attroff(curses.color_pair(C_DIVIDER) | curses.A_BOLD)

        with self._lock:
            contacts = list(self.contacts)
            sel_idx  = self.selected_idx

        # Scroll so selected is visible
        visible_start = max(0, sel_idx - list_h + 1)
        visible = contacts[visible_start: visible_start + list_h]

        for i, c in enumerate(visible):
            y = y0 + i
            abs_idx = visible_start + i
            name    = c.get("display_name") or _short_hash(c["dest_hash"])
            unread  = c.get("unread", 0)

            # Truncate name
            max_name = cw - 4
            if len(name) > max_name:
                name = name[:max_name - 1] + "…"

            line = f" {name}"
            if unread:
                badge = str(unread) if unread < 100 else "99+"
                line = line.ljust(cw - len(badge) - 1) + badge

            if abs_idx == sel_idx:
                attr = curses.color_pair(C_SELECTED) | curses.A_BOLD
                prefix = ">"
            else:
                attr = curses.color_pair(C_UNREAD) | curses.A_BOLD if unread else C_NORMAL
                prefix = " "

            _addstr_clipped(self.stdscr, y, 0, f"{prefix}{line.ljust(cw - 1)}", attr)

    def _draw_divider(self, cw, h):
        for y in range(self.HEADER_HEIGHT, h - self.HELP_HEIGHT - 1):
            try:
                self.stdscr.addch(y, cw, curses.ACS_VLINE,
                                  curses.color_pair(C_DIVIDER))
            except curses.error:
                pass

    def _draw_messages(self, x0, mw, area_h):
        y0    = self.HEADER_HEIGHT + 1
        h, _  = self.stdscr.getmaxyx()

        # Pane title
        title = " Messages"
        if self.selected_peer:
            with self._lock:
                contacts = self.contacts
            peer_name = next(
                (c.get("display_name") or _short_hash(c["dest_hash"])
                 for c in contacts if c["dest_hash"] == self.selected_peer),
                _short_hash(self.selected_peer)
            )
            title = f" {peer_name} [{_short_hash(self.selected_peer)}]"

        if self.focus == "messages":
            title += " ◄"

        self.stdscr.attron(curses.color_pair(C_DIVIDER) | curses.A_BOLD)
        _addstr_clipped(self.stdscr, self.HEADER_HEIGHT, x0, title[:mw - 1].ljust(mw - 1))
        self.stdscr.attroff(curses.color_pair(C_DIVIDER) | curses.A_BOLD)

        if not self.selected_peer:
            _addstr_clipped(self.stdscr, y0 + 2, x0 + 2,
                            "No conversation selected.", curses.A_DIM)
            _addstr_clipped(self.stdscr, y0 + 3, x0 + 2,
                            "Press N to start a new conversation.", curses.A_DIM)
            return

        with self._lock:
            messages = list(self.messages)
            my_hash  = (self.core.identity_hash or "").strip("<>")

        if not messages:
            _addstr_clipped(self.stdscr, y0 + 2, x0 + 2,
                            "No messages yet. Press / to compose.", curses.A_DIM)
            return

        # Build rendered lines (wrap long messages)
        # Each entry: (line_text, attr)
        rendered: list[tuple[str, int]] = []
        for msg in messages:
            ts    = _ts(msg["timestamp"])
            is_me = msg["direction"] == "out"
            name  = "You" if is_me else peer_name
            has_img = bool(msg.get("has_image"))
            content = msg.get("content", "")
            state   = msg.get("state", "")

            # State indicator for outbound
            state_icon = ""
            if is_me:
                state_icon = {"pending": " ○", "sent": " ◎",
                              "delivered": " ●", "failed": " ✗"}.get(state, "")

            img_note = " [📷]" if has_img else ""
            prefix   = f"  {ts}  {name}: "
            suffix   = f"{img_note}{state_icon}"
            body     = content + suffix

            attr_ts   = curses.color_pair(C_TIMESTAMP)
            attr_msg  = curses.color_pair(C_SELF) if is_me else C_NORMAL

            # Wrap body to available width
            wrap_w = max(mw - len(prefix) - 2, 10)
            lines  = textwrap.wrap(body, wrap_w) if body.strip() else [""]

            for j, line in enumerate(lines):
                if j == 0:
                    rendered.append((f"{prefix}{line}", attr_msg))
                else:
                    rendered.append((f"{'':>{len(prefix)}}{line}", attr_msg))

        # Scroll
        total = len(rendered)
        scroll = _clamp(self.msg_scroll, 0, max(0, total - area_h))
        self.msg_scroll = scroll

        visible_lines = rendered[max(0, total - area_h - scroll):
                                 total - scroll if scroll else None]

        for i, (line, attr) in enumerate(visible_lines[-area_h:]):
            _addstr_clipped(self.stdscr, y0 + i, x0, line, attr)

        # Scroll indicator
        if total > area_h:
            pct = int(100 * (total - area_h - scroll) / max(1, total - area_h))
            _addstr_clipped(self.stdscr, y0, x0 + mw - 8,
                            f"[{pct:3d}%]", curses.A_DIM)

    def _draw_compose(self, w, h):
        cy = h - self.HELP_HEIGHT - self.COMPOSE_HEIGHT - 1

        # Status flash (show for 4 seconds)
        if self.status_msg and time.time() - self.status_ts < 4.0:
            _addstr_clipped(self.stdscr, cy - 1, 1,
                            self.status_msg[:w - 2], curses.A_DIM)

        if self.new_conv_mode:
            prompt = " New conversation hash > "
            line   = prompt + self.new_conv_buf
            attr   = curses.color_pair(C_INPUT) | curses.A_BOLD
            _addstr_clipped(self.stdscr, cy, 0, " " * w, attr)
            _addstr_clipped(self.stdscr, cy, 0, line[:w - 1], attr)
            try:
                curses.curs_set(1)
                self.stdscr.move(cy, min(len(line), w - 2))
            except curses.error:
                pass
            return

        prompt = " > "
        visible_buf = self.compose_buf
        # Scroll text if too long for window
        max_buf = w - len(prompt) - 2
        if len(visible_buf) > max_buf:
            visible_buf = visible_buf[len(visible_buf) - max_buf:]

        attr = curses.color_pair(C_INPUT) if self.focus == "compose" else curses.A_NORMAL
        _addstr_clipped(self.stdscr, cy, 0, " " * w, curses.color_pair(C_INPUT))
        _addstr_clipped(self.stdscr, cy, 0, prompt + visible_buf, attr)

    def _draw_help(self, w, h):
        hy = h - 1
        if self.new_conv_mode:
            help_text = "  Enter:confirm  Esc:cancel"
        elif self.focus == "compose":
            help_text = "  Enter:send  Esc:cancel  Ctrl+A:attach(TODO)"
        elif self.focus == "contacts":
            help_text = "  ↑↓:navigate  Enter:select  N:new  D:delete  Tab:messages  A:announce  Q:quit"
        else:
            help_text = "  ↑↓:scroll  /:compose  Tab:contacts  A:announce  Q:quit"

        self.stdscr.attron(curses.color_pair(C_HELP))
        _addstr_clipped(self.stdscr, hy, 0, " " * w)
        _addstr_clipped(self.stdscr, hy, 0, help_text[:w - 1])
        self.stdscr.attroff(curses.color_pair(C_HELP))

    # ── Input handling ────────────────────────────────────────────────────────

    def _handle_input(self):
        try:
            key = self.stdscr.getch()
        except curses.error:
            return

        if key == -1:
            return

        if self.new_conv_mode:
            self._handle_new_conv_input(key)
            return

        if self.focus == "compose":
            self._handle_compose_input(key)
        elif self.focus == "contacts":
            self._handle_contacts_input(key)
        elif self.focus == "messages":
            self._handle_messages_input(key)

    def _handle_contacts_input(self, key):
        with self._lock:
            n = len(self.contacts)

        if key in (curses.KEY_UP, ord('k')):
            self._select_contact(self.selected_idx - 1)

        elif key in (curses.KEY_DOWN, ord('j')):
            self._select_contact(self.selected_idx + 1)

        elif key in (curses.KEY_ENTER, ord('\n'), ord('\r')):
            self._select_contact(self.selected_idx)
            self.focus = "messages"

        elif key == ord('\t'):
            if self.selected_peer:
                self.focus = "messages"

        elif key in (ord('n'), ord('N')):
            self.new_conv_mode = True
            self.new_conv_buf  = ""

        elif key in (ord('d'), ord('D')):
            if self.selected_peer:
                self.db.clear_conversation(self.selected_peer)
                self._set_status(f"Conversation cleared.")
                self._refresh_contacts()
                self.selected_peer = None
                self.messages = []

        elif key in (ord('a'), ord('A')):
            self.core.announce()
            self._set_status("Announced!")

        elif key in (ord('/'),):
            if self.selected_peer:
                self.focus = "compose"

        elif key in (ord('q'), ord('Q')):
            self.running = False

    def _handle_messages_input(self, key):
        h, _ = self.stdscr.getmaxyx() if hasattr(self, 'stdscr') else (24, 80)
        area_h = h - self.HEADER_HEIGHT - self.COMPOSE_HEIGHT - self.HELP_HEIGHT - 2

        if key in (curses.KEY_UP, ord('k')):
            self.msg_scroll = min(self.msg_scroll + 1,
                                  max(0, len(self.messages) - area_h))

        elif key in (curses.KEY_DOWN, ord('j')):
            self.msg_scroll = max(0, self.msg_scroll - 1)

        elif key in (curses.KEY_PPAGE,):  # Page Up
            self.msg_scroll = min(self.msg_scroll + area_h,
                                  max(0, len(self.messages) - area_h))

        elif key in (curses.KEY_NPAGE,):  # Page Down
            self.msg_scroll = max(0, self.msg_scroll - area_h)

        elif key == ord('\t'):
            self.focus = "contacts"

        elif key in (ord('/'), ord('i')):
            if self.selected_peer:
                self.focus = "compose"

        elif key in (ord('a'), ord('A')):
            self.core.announce()
            self._set_status("Announced!")

        elif key in (ord('q'), ord('Q')):
            self.running = False

        elif key == 27:  # Escape
            self.focus = "contacts"

    def _handle_compose_input(self, key):
        if key in (curses.KEY_ENTER, ord('\n'), ord('\r')):
            self._send_composed()

        elif key == 27:  # Escape
            self.compose_buf    = ""
            self.compose_cursor = 0
            self.focus = "messages"

        elif key in (curses.KEY_BACKSPACE, 127, 8):
            if self.compose_cursor > 0:
                self.compose_buf = (
                    self.compose_buf[:self.compose_cursor - 1]
                    + self.compose_buf[self.compose_cursor:]
                )
                self.compose_cursor -= 1

        elif key == curses.KEY_LEFT:
            self.compose_cursor = max(0, self.compose_cursor - 1)

        elif key == curses.KEY_RIGHT:
            self.compose_cursor = min(len(self.compose_buf), self.compose_cursor + 1)

        elif key == curses.KEY_HOME or key == 1:  # Ctrl+A
            self.compose_cursor = 0

        elif key == curses.KEY_END or key == 5:   # Ctrl+E
            self.compose_cursor = len(self.compose_buf)

        elif 32 <= key <= 126:   # printable ASCII
            ch = chr(key)
            self.compose_buf = (
                self.compose_buf[:self.compose_cursor]
                + ch
                + self.compose_buf[self.compose_cursor:]
            )
            self.compose_cursor += 1

    def _handle_new_conv_input(self, key):
        if key in (curses.KEY_ENTER, ord('\n'), ord('\r')):
            h = self.new_conv_buf.strip().replace("<", "").replace(">", "").lower()
            if len(h) == 32:
                self.db.upsert_contact(h, display_name=_short_hash(h))
                self._refresh_contacts()
                # Select the new contact
                with self._lock:
                    for i, c in enumerate(self.contacts):
                        if c["dest_hash"] == h:
                            self.selected_idx  = i
                            self.selected_peer = h
                            break
                self._set_status(f"New conversation: {h[:16]}…")
                self.new_conv_mode = False
                self.focus = "compose"
            else:
                self._set_status("Hash must be 32 hex chars. Try again.")
                self.new_conv_buf = ""

        elif key == 27:
            self.new_conv_mode = False
            self.new_conv_buf  = ""

        elif key in (curses.KEY_BACKSPACE, 127, 8):
            self.new_conv_buf = self.new_conv_buf[:-1]

        elif 32 <= key <= 126:
            self.new_conv_buf += chr(key)

    def _send_composed(self):
        text = self.compose_buf.strip()
        if not text or not self.selected_peer:
            return

        msg_id = self.core.send_text(self.selected_peer, text)
        if msg_id:
            self.compose_buf    = ""
            self.compose_cursor = 0
            self.focus = "messages"
            self.msg_scroll = 0
            self._refresh_messages()
            self._set_status(f"Sent → {_short_hash(self.selected_peer)}")
        else:
            self._set_status("Send failed — check connection.")


# ── Entry point ───────────────────────────────────────────────────────────────

def run_tui(core):
    """
    Launch the TUI.  Call after core.start().

        from nano.tui import run_tui
        run_tui(core)
    """
    tui = NanoTUI(core)
    tui.run()
