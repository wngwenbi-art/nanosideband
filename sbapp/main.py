"""
sbapp/main.py
NanoSideband Android App — Kivy UI

Screens
-------
  SplashScreen      → startup / identity init
  ConversationsScreen → conversation list
  MessagesScreen    → message thread
  ComposeScreen     → new conversation
  RNodeScreen       → RNode BT setup & config
  SettingsScreen    → display name, announce interval
"""

import os
import sys
import threading
import time

# ── Platform detection ────────────────────────────────────────────────────────
ANDROID = False
try:
    from android import mActivity  # noqa
    ANDROID = True
except ImportError:
    pass

# ── Kivy config (must be before kivy imports) ─────────────────────────────────
os.environ.setdefault("KIVY_NO_ENV_CONFIG", "1")

from kivy.config import Config
Config.set("graphics", "resizable", "1")
Config.set("kivy", "log_level", "warning")

from kivy.app import App
from kivy.clock import Clock
from kivy.lang import Builder
from kivy.uix.screenmanager import ScreenManager, Screen, SlideTransition
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.textinput import TextInput
from kivy.uix.popup import Popup
from kivy.uix.spinner import Spinner
from kivy.properties import StringProperty, BooleanProperty, ListProperty
from kivy.metrics import dp
from kivy.utils import platform

# ── App data directory ────────────────────────────────────────────────────────
if ANDROID:
    from android.storage import app_storage_path  # type: ignore
    APP_DIR = app_storage_path()
else:
    APP_DIR = os.path.join(os.path.expanduser("~"), ".nanosideband")

os.makedirs(APP_DIR, exist_ok=True)

# ── Add nano package to path ──────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# ── KV layout string ─────────────────────────────────────────────────────────

KV = """
#:import dp kivy.metrics.dp
#:import Clock kivy.clock.Clock

<NsButton@Button>:
    background_color: 0.91, 0.27, 0.37, 1
    background_normal: ''
    color: 1, 1, 1, 1
    font_size: dp(15)
    bold: True
    size_hint_y: None
    height: dp(46)

<NsSecondaryButton@Button>:
    background_color: 0.09, 0.2, 0.38, 1
    background_normal: ''
    color: 0.88, 0.88, 0.88, 1
    font_size: dp(15)
    size_hint_y: None
    height: dp(46)

<NsInput@TextInput>:
    background_color: 0.06, 0.19, 0.38, 1
    foreground_color: 0.88, 0.88, 0.88, 1
    cursor_color: 0.91, 0.27, 0.37, 1
    font_size: dp(15)
    padding: [dp(12), dp(10)]
    size_hint_y: None
    height: dp(46)
    multiline: False

<NsLabel@Label>:
    color: 0.88, 0.88, 0.88, 1
    font_size: dp(14)
    text_size: self.width, None
    size_hint_y: None
    height: self.texture_size[1]

<SplashScreen>:
    name: 'splash'
    canvas.before:
        Color:
            rgba: 0.1, 0.1, 0.18, 1
        Rectangle:
            pos: self.pos
            size: self.size
    BoxLayout:
        orientation: 'vertical'
        padding: dp(40)
        spacing: dp(20)
        Widget:
            size_hint_y: 0.3
        Label:
            text: 'NanoSideband'
            font_size: dp(32)
            bold: True
            color: 0.91, 0.27, 0.37, 1
            size_hint_y: None
            height: dp(50)
        Label:
            text: root.status_text
            font_size: dp(14)
            color: 0.63, 0.63, 0.69, 1
            size_hint_y: None
            height: dp(30)
        Widget:
            size_hint_y: 0.4

<ConversationsScreen>:
    name: 'conversations'
    canvas.before:
        Color:
            rgba: 0.1, 0.1, 0.18, 1
        Rectangle:
            pos: self.pos
            size: self.size
    BoxLayout:
        orientation: 'vertical'
        # Header
        BoxLayout:
            size_hint_y: None
            height: dp(56)
            padding: dp(12), dp(8)
            spacing: dp(8)
            canvas.before:
                Color:
                    rgba: 0.06, 0.13, 0.24, 1
                Rectangle:
                    pos: self.pos
                    size: self.size
            Label:
                text: 'NanoSideband'
                font_size: dp(20)
                bold: True
                color: 0.91, 0.27, 0.37, 1
                size_hint_x: 1
                halign: 'left'
                valign: 'middle'
                text_size: self.size
            NsSecondaryButton:
                text: '📡'
                size_hint_x: None
                width: dp(46)
                on_release: root.do_announce()
            NsSecondaryButton:
                text: '⚙'
                size_hint_x: None
                width: dp(46)
                on_release: root.goto_settings()
            NsSecondaryButton:
                text: '📻'
                size_hint_x: None
                width: dp(46)
                on_release: root.goto_rnode()
        # Conversation list
        ScrollView:
            id: scroll
            do_scroll_x: False
            BoxLayout:
                id: conv_list
                orientation: 'vertical'
                spacing: dp(1)
                size_hint_y: None
                height: self.minimum_height
                padding: 0, 0, 0, dp(80)
        # FAB-style new button
        FloatLayout:
            size_hint_y: None
            height: 0
            NsButton:
                text: '+ New Conversation'
                size_hint: None, None
                size: dp(220), dp(46)
                pos_hint: {'center_x': 0.5, 'y': dp(16)/self.height if self.height else 0}
                pos: self.parent.width/2 - dp(110), dp(16)
                on_release: root.goto_new()

<MessagesScreen>:
    name: 'messages'
    canvas.before:
        Color:
            rgba: 0.1, 0.1, 0.18, 1
        Rectangle:
            pos: self.pos
            size: self.size
    BoxLayout:
        orientation: 'vertical'
        # Header
        BoxLayout:
            size_hint_y: None
            height: dp(56)
            padding: dp(8), dp(8)
            spacing: dp(8)
            canvas.before:
                Color:
                    rgba: 0.06, 0.13, 0.24, 1
                Rectangle:
                    pos: self.pos
                    size: self.size
            NsSecondaryButton:
                text: '←'
                size_hint_x: None
                width: dp(46)
                on_release: root.go_back()
            Label:
                text: root.peer_name
                font_size: dp(16)
                bold: True
                color: 0.88, 0.88, 0.88, 1
                halign: 'left'
                valign: 'middle'
                text_size: self.size
        # Messages
        ScrollView:
            id: msg_scroll
            do_scroll_x: False
            BoxLayout:
                id: msg_list
                orientation: 'vertical'
                spacing: dp(6)
                padding: dp(10), dp(10)
                size_hint_y: None
                height: self.minimum_height
        # Compose
        BoxLayout:
            size_hint_y: None
            height: dp(56)
            padding: dp(8), dp(6)
            spacing: dp(6)
            canvas.before:
                Color:
                    rgba: 0.06, 0.13, 0.24, 1
                Rectangle:
                    pos: self.pos
                    size: self.size
            NsInput:
                id: compose_input
                hint_text: 'Type a message…'
                size_hint_x: 1
                height: dp(42)
                on_text_validate: root.send_message()
            NsButton:
                text: 'Send'
                size_hint_x: None
                width: dp(80)
                height: dp(42)
                on_release: root.send_message()

<NewConvScreen>:
    name: 'new_conv'
    canvas.before:
        Color:
            rgba: 0.1, 0.1, 0.18, 1
        Rectangle:
            pos: self.pos
            size: self.size
    BoxLayout:
        orientation: 'vertical'
        padding: dp(20)
        spacing: dp(14)
        BoxLayout:
            size_hint_y: None
            height: dp(56)
            NsSecondaryButton:
                text: '←'
                size_hint_x: None
                width: dp(46)
                on_release: root.go_back()
            Label:
                text: 'New Conversation'
                font_size: dp(18)
                bold: True
                color: 0.88, 0.88, 0.88, 1
        Label:
            text: 'Destination hash (32 hex chars):'
            font_size: dp(14)
            color: 0.63, 0.63, 0.69, 1
            size_hint_y: None
            height: dp(24)
            halign: 'left'
            text_size: self.size
        NsInput:
            id: hash_input
            hint_text: 'e.g. a3f2b1c4d5e6...'
            font_name: 'RobotoMono-Regular' if False else ''
        Label:
            id: error_label
            text: ''
            color: 0.91, 0.27, 0.37, 1
            font_size: dp(13)
            size_hint_y: None
            height: dp(24)
        NsButton:
            text: 'Start Conversation'
            on_release: root.start_conv()
        Widget:

<RNodeScreen>:
    name: 'rnode'
    canvas.before:
        Color:
            rgba: 0.1, 0.1, 0.18, 1
        Rectangle:
            pos: self.pos
            size: self.size
    BoxLayout:
        orientation: 'vertical'
        padding: dp(16)
        spacing: dp(12)
        BoxLayout:
            size_hint_y: None
            height: dp(56)
            NsSecondaryButton:
                text: '←'
                size_hint_x: None
                width: dp(46)
                on_release: root.go_back()
            Label:
                text: 'RNode Bluetooth Setup'
                font_size: dp(18)
                bold: True
                color: 0.88, 0.88, 0.88, 1
        Label:
            text: 'Paired Bluetooth Devices:'
            font_size: dp(14)
            color: 0.63, 0.63, 0.69, 1
            size_hint_y: None
            height: dp(24)
            halign: 'left'
            text_size: self.size
        Spinner:
            id: bt_spinner
            text: 'Select device…'
            values: root.bt_devices
            size_hint_y: None
            height: dp(46)
            background_color: 0.06, 0.19, 0.38, 1
            color: 0.88, 0.88, 0.88, 1
            font_size: dp(14)
        NsSecondaryButton:
            text: '🔄 Refresh devices'
            on_release: root.refresh_devices()
        Label:
            text: 'RNode Parameters:'
            font_size: dp(14)
            color: 0.63, 0.63, 0.69, 1
            size_hint_y: None
            height: dp(28)
            halign: 'left'
            text_size: self.size
        GridLayout:
            cols: 2
            spacing: dp(8)
            size_hint_y: None
            height: dp(200)
            Label:
                text: 'Frequency (MHz):'
                color: 0.88,0.88,0.88,1
                font_size: dp(14)
            NsInput:
                id: freq_input
                text: root.rnode_freq
                hint_text: '915.0'
            Label:
                text: 'Bandwidth (kHz):'
                color: 0.88,0.88,0.88,1
                font_size: dp(14)
            NsInput:
                id: bw_input
                text: root.rnode_bw
                hint_text: '125'
            Label:
                text: 'Spreading Factor:'
                color: 0.88,0.88,0.88,1
                font_size: dp(14)
            NsInput:
                id: sf_input
                text: root.rnode_sf
                hint_text: '8'
            Label:
                text: 'TX Power (dBm):'
                color: 0.88,0.88,0.88,1
                font_size: dp(14)
            NsInput:
                id: txp_input
                text: root.rnode_txp
                hint_text: '17'
        Label:
            id: rnode_status
            text: root.rnode_status
            color: 0.91, 0.27, 0.37, 1
            font_size: dp(13)
            size_hint_y: None
            height: dp(28)
        BoxLayout:
            size_hint_y: None
            height: dp(46)
            spacing: dp(8)
            NsSecondaryButton:
                text: 'Test Connection'
                on_release: root.test_connection()
            NsButton:
                text: 'Save & Connect'
                on_release: root.save_and_connect()
        Widget:

<SettingsScreen>:
    name: 'settings'
    canvas.before:
        Color:
            rgba: 0.1, 0.1, 0.18, 1
        Rectangle:
            pos: self.pos
            size: self.size
    BoxLayout:
        orientation: 'vertical'
        padding: dp(16)
        spacing: dp(12)
        BoxLayout:
            size_hint_y: None
            height: dp(56)
            NsSecondaryButton:
                text: '←'
                size_hint_x: None
                width: dp(46)
                on_release: root.go_back()
            Label:
                text: 'Settings'
                font_size: dp(18)
                bold: True
                color: 0.88, 0.88, 0.88, 1
        Label:
            text: 'Display Name:'
            font_size: dp(14)
            color: 0.63, 0.63, 0.69, 1
            size_hint_y: None
            height: dp(24)
            halign: 'left'
            text_size: self.size
        NsInput:
            id: name_input
            text: root.display_name
            hint_text: 'Your name'
        Label:
            text: 'Your Identity Hash:'
            font_size: dp(14)
            color: 0.63, 0.63, 0.69, 1
            size_hint_y: None
            height: dp(24)
            halign: 'left'
            text_size: self.size
        Label:
            text: root.identity_hash
            font_size: dp(12)
            color: 0.63, 0.63, 0.69, 1
            size_hint_y: None
            height: dp(24)
        NsButton:
            text: 'Save Settings'
            on_release: root.save_settings()
        Widget:
"""


# ── Screen implementations ────────────────────────────────────────────────────

class SplashScreen(Screen):
    status_text = StringProperty("Starting…")


class ConversationsScreen(Screen):

    def on_enter(self):
        Clock.schedule_once(lambda dt: self.refresh(), 0.1)

    def refresh(self):
        app = App.get_running_app()
        box = self.ids.conv_list
        box.clear_widgets()

        if not app.core:
            box.add_widget(Label(
                text="RNS not connected.\nConfigure RNode in settings.",
                font_size=dp(14), color=(0.63, 0.63, 0.69, 1),
                halign="center", size_hint_y=None, height=dp(80)
            ))
            return

        convs = app.db.list_conversations() if app.db else []
        contacts = {c["dest_hash"]: c for c in (app.db.list_contacts() if app.db else [])}

        if not convs:
            box.add_widget(Label(
                text="No conversations yet.\nPress '+ New Conversation' to start one,\nor announce yourself to be discovered.",
                font_size=dp(14), color=(0.63, 0.63, 0.69, 1),
                halign="center", size_hint_y=None, height=dp(100)
            ))
            return

        convs.sort(key=lambda c: c.get("last_ts", 0), reverse=True)
        for conv in convs:
            peer = conv["peer"]
            name = contacts.get(peer, {}).get("display_name") or peer[:12] + "…"
            unread = conv.get("unread", 0)
            badge = f" [{unread}]" if unread else ""

            btn = Button(
                text=f"{name}{badge}\n[size=12sp][color=aaaaaa]{peer[:16]}…[/color][/size]",
                markup=True,
                size_hint_y=None, height=dp(64),
                background_normal="", background_color=(0.09, 0.2, 0.38, 1),
                halign="left", valign="middle",
                padding=(dp(16), 0),
            )
            btn.bind(on_release=lambda b, p=peer, n=name: self.open_conv(p, n))
            box.add_widget(btn)

    def open_conv(self, peer_hash, name):
        app = App.get_running_app()
        app.current_peer = peer_hash
        app.current_peer_name = name
        if app.db:
            app.db.mark_read(peer_hash)
        app.sm.transition = SlideTransition(direction="left")
        app.sm.current = "messages"

    def do_announce(self):
        app = App.get_running_app()
        if app.core:
            app.core.announce()
            self._toast("Announced!")

    def goto_settings(self):
        app = App.get_running_app()
        app.sm.transition = SlideTransition(direction="left")
        app.sm.current = "settings"

    def goto_rnode(self):
        app = App.get_running_app()
        app.sm.transition = SlideTransition(direction="left")
        app.sm.current = "rnode"

    def goto_new(self):
        app = App.get_running_app()
        app.sm.transition = SlideTransition(direction="left")
        app.sm.current = "new_conv"

    def _toast(self, msg):
        popup = Popup(
            title="", content=Label(text=msg, color=(0.88, 0.88, 0.88, 1)),
            size_hint=(0.6, None), height=dp(80),
            background_color=(0.06, 0.13, 0.24, 1),
        )
        popup.open()
        Clock.schedule_once(lambda dt: popup.dismiss(), 2)


class MessagesScreen(Screen):
    peer_name = StringProperty("Conversation")

    def on_enter(self):
        app = App.get_running_app()
        self.peer_name = app.current_peer_name or app.current_peer[:12] + "…"
        Clock.schedule_once(lambda dt: self.refresh(), 0.1)

    def refresh(self):
        app = App.get_running_app()
        if not app.db or not app.current_peer:
            return

        box = self.ids.msg_list
        box.clear_widgets()
        msgs = app.db.list_messages(app.current_peer, limit=80)
        msgs.reverse()

        for m in msgs:
            direction = m["direction"]
            content = m.get("content") or ""
            state = m.get("state", "")
            ts = time.strftime("%H:%M", time.localtime(m["timestamp"]))
            state_icons = {"pending": "○", "sent": "◎", "delivered": "●", "failed": "✗"}
            icon = state_icons.get(state, "") if direction == "out" else ""
            img_note = " [📷]" if m.get("has_image") else ""

            full_text = f"{content}{img_note}\n[size=11sp][color=888888]{ts} {icon}[/color][/size]"
            bg = (0.12, 0.18, 0.29, 1) if direction == "in" else (0.1, 0.23, 0.16, 1)
            halign = "left" if direction == "in" else "right"

            lbl = Label(
                text=full_text, markup=True,
                size_hint_y=None, size_hint_x=0.8,
                halign=halign, valign="top",
                color=(0.88, 0.88, 0.88, 1),
                font_size=dp(14),
                padding=(dp(12), dp(8)),
            )
            lbl.bind(width=lambda l, w: setattr(l, "text_size", (w, None)))
            lbl.bind(texture_size=lambda l, s: setattr(l, "height", s[1] + dp(16)))

            row = BoxLayout(size_hint_y=None, height=dp(60))
            row.bind(minimum_height=row.setter("height"))
            if direction == "out":
                row.add_widget(Widget())
            with lbl.canvas.before:
                from kivy.graphics import Color as KColor, RoundedRectangle
                KColor(rgba=bg)
                lbl._bg_rect = RoundedRectangle(radius=[dp(10)], pos=lbl.pos, size=lbl.size)
            lbl.bind(pos=lambda l, v: setattr(l._bg_rect, "pos", v))
            lbl.bind(size=lambda l, v: setattr(l._bg_rect, "size", v))
            row.add_widget(lbl)
            if direction == "in":
                row.add_widget(Widget())
            box.add_widget(row)

        Clock.schedule_once(lambda dt: self._scroll_bottom(), 0.1)

    def _scroll_bottom(self):
        sv = self.ids.msg_scroll
        sv.scroll_y = 0

    def send_message(self):
        app = App.get_running_app()
        inp = self.ids.compose_input
        text = inp.text.strip()
        if not text or not app.current_peer:
            return
        if not app.core:
            self._toast("Not connected to RNS")
            return
        inp.text = ""
        threading.Thread(
            target=app.core.send_text,
            args=(app.current_peer, text),
            daemon=True
        ).start()
        Clock.schedule_once(lambda dt: self.refresh(), 0.3)

    def go_back(self):
        app = App.get_running_app()
        app.sm.transition = SlideTransition(direction="right")
        app.sm.current = "conversations"

    def _toast(self, msg):
        popup = Popup(
            title="", content=Label(text=msg, color=(0.88, 0.88, 0.88, 1)),
            size_hint=(0.6, None), height=dp(80),
        )
        popup.open()
        Clock.schedule_once(lambda dt: popup.dismiss(), 2)


class NewConvScreen(Screen):

    def start_conv(self):
        app = App.get_running_app()
        h = self.ids.hash_input.text.strip().lower().replace("<", "").replace(">", "")
        if len(h) != 32:
            self.ids.error_label.text = "Hash must be exactly 32 hex characters"
            return
        if app.db:
            app.db.upsert_contact(h)
        app.current_peer = h
        app.current_peer_name = h[:12] + "…"
        app.sm.transition = SlideTransition(direction="left")
        app.sm.current = "messages"
        self.ids.hash_input.text = ""
        self.ids.error_label.text = ""

    def go_back(self):
        app = App.get_running_app()
        app.sm.transition = SlideTransition(direction="right")
        app.sm.current = "conversations"


class RNodeScreen(Screen):
    bt_devices = ListProperty([])
    rnode_freq = StringProperty("915.0")
    rnode_bw   = StringProperty("125")
    rnode_sf   = StringProperty("8")
    rnode_txp  = StringProperty("17")
    rnode_status = StringProperty("")

    def on_enter(self):
        self._load_config()
        Clock.schedule_once(lambda dt: self.refresh_devices(), 0.2)

    def _load_config(self):
        app = App.get_running_app()
        cfg = app.config_data
        self.rnode_freq = str(cfg.get("rnode_freq", "915.0"))
        self.rnode_bw   = str(cfg.get("rnode_bw",   "125"))
        self.rnode_sf   = str(cfg.get("rnode_sf",   "8"))
        self.rnode_txp  = str(cfg.get("rnode_txp",  "17"))

    def refresh_devices(self):
        self.rnode_status = "Scanning…"
        threading.Thread(target=self._scan_bt, daemon=True).start()

    def _scan_bt(self):
        devices = []
        try:
            if ANDROID:
                from jnius import autoclass  # type: ignore
                BluetoothAdapter = autoclass("android.bluetooth.BluetoothAdapter")
                adapter = BluetoothAdapter.getDefaultAdapter()
                if adapter:
                    paired = adapter.getBondedDevices().toArray()
                    for d in paired:
                        devices.append(f"{d.getName()} [{d.getAddress()}]")
            else:
                # Desktop fallback — list via pybluetooth if available
                try:
                    import bluetooth  # type: ignore
                    nearby = bluetooth.discover_devices(duration=4, lookup_names=True)
                    devices = [f"{name} [{addr}]" for addr, name in nearby]
                except Exception:
                    devices = ["(no bluetooth devices found)"]
        except Exception as e:
            devices = [f"Error: {e}"]

        def _update(dt):
            self.bt_devices = devices if devices else ["(no paired devices)"]
            self.rnode_status = f"Found {len(devices)} device(s)"
        Clock.schedule_once(_update, 0)

    def test_connection(self):
        sel = self.ids.bt_spinner.text
        if not sel or sel.startswith("Select"):
            self.rnode_status = "Select a device first"
            return
        self.rnode_status = "Testing…"
        threading.Thread(target=self._do_test, args=(sel,), daemon=True).start()

    def _do_test(self, device_str):
        try:
            addr = self._extract_addr(device_str)
            if ANDROID:
                from jnius import autoclass  # type: ignore
                BluetoothAdapter = autoclass("android.bluetooth.BluetoothAdapter")
                BluetoothDevice = autoclass("android.bluetooth.BluetoothDevice")
                UUID = autoclass("java.util.UUID")
                adapter = BluetoothAdapter.getDefaultAdapter()
                # Serial port profile UUID
                SPP_UUID = "00001101-0000-1000-8000-00805F9B34FB"
                device = adapter.getRemoteDevice(addr)
                socket = device.createInsecureRfcommSocketToServiceRecord(
                    UUID.fromString(SPP_UUID)
                )
                socket.connect()
                # Send RNode ping (0x3A = getconf)
                out = socket.getOutputStream()
                out.write([0x3A])
                out.flush()
                time.sleep(0.3)
                inp = socket.getInputStream()
                resp = bytearray()
                while inp.available() > 0:
                    resp.append(inp.read())
                socket.close()
                if resp:
                    msg = f"RNode responded ({len(resp)} bytes) ✓"
                else:
                    msg = "Connected but no response (may still work)"
            else:
                msg = f"Desktop: would connect to {addr}"
        except Exception as e:
            msg = f"Connection failed: {e}"
        Clock.schedule_once(lambda dt: setattr(self, "rnode_status", msg), 0)

    def save_and_connect(self):
        sel = self.ids.bt_spinner.text
        if not sel or sel.startswith("Select"):
            self.rnode_status = "Select a device first"
            return
        addr = self._extract_addr(sel)
        app = App.get_running_app()
        app.config_data.update({
            "rnode_bt_addr": addr,
            "rnode_bt_name": sel.split(" [")[0],
            "rnode_freq": self.ids.freq_input.text,
            "rnode_bw":   self.ids.bw_input.text,
            "rnode_sf":   self.ids.sf_input.text,
            "rnode_txp":  self.ids.txp_input.text,
        })
        app._save_config()
        self.rnode_status = f"Saved. Reconnecting to {addr}…"
        # Restart RNS with new interface
        threading.Thread(target=app._restart_rns, daemon=True).start()
        Clock.schedule_once(lambda dt: self.go_back(), 2)

    def go_back(self):
        app = App.get_running_app()
        app.sm.transition = SlideTransition(direction="right")
        app.sm.current = "conversations"

    @staticmethod
    def _extract_addr(device_str):
        """Extract BT MAC address from 'Name [AA:BB:CC:DD:EE:FF]'."""
        try:
            return device_str.split("[")[1].rstrip("]")
        except Exception:
            return device_str


class SettingsScreen(Screen):
    display_name  = StringProperty("")
    identity_hash = StringProperty("")

    def on_enter(self):
        app = App.get_running_app()
        self.display_name  = app.config_data.get("display_name", "")
        self.identity_hash = (app.core.identity_hash if app.core else "Not initialized") or ""

    def save_settings(self):
        app = App.get_running_app()
        app.config_data["display_name"] = self.ids.name_input.text.strip()
        app._save_config()
        if app.core:
            app.core.config["display_name"] = app.config_data["display_name"]
        self._toast("Saved!")

    def go_back(self):
        app = App.get_running_app()
        app.sm.transition = SlideTransition(direction="right")
        app.sm.current = "conversations"

    def _toast(self, msg):
        popup = Popup(
            title="", content=Label(text=msg, color=(0.88, 0.88, 0.88, 1)),
            size_hint=(0.6, None), height=dp(80),
        )
        popup.open()
        Clock.schedule_once(lambda dt: popup.dismiss(), 2)


# ── Main App ──────────────────────────────────────────────────────────────────

class NanoSidebandApp(App):
    title = "NanoSideband"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.core = None
        self.db   = None
        self.sm   = None
        self.current_peer      = ""
        self.current_peer_name = ""
        self.config_data       = {}

    def build(self):
        Builder.load_string(KV)
        self.sm = ScreenManager()
        self.sm.add_widget(SplashScreen())
        self.sm.add_widget(ConversationsScreen())
        self.sm.add_widget(MessagesScreen())
        self.sm.add_widget(NewConvScreen())
        self.sm.add_widget(RNodeScreen())
        self.sm.add_widget(SettingsScreen())
        self.sm.current = "splash"
        # Request Android permissions
        if ANDROID:
            self._request_permissions()
        Clock.schedule_once(self._init_core, 0.5)
        return self.sm

    def _request_permissions(self):
        try:
            from android.permissions import request_permissions, Permission  # type: ignore
            request_permissions([
                Permission.BLUETOOTH,
                Permission.BLUETOOTH_ADMIN,
                Permission.BLUETOOTH_CONNECT,
                Permission.BLUETOOTH_SCAN,
                Permission.ACCESS_FINE_LOCATION,
                Permission.READ_EXTERNAL_STORAGE,
                Permission.WRITE_EXTERNAL_STORAGE,
            ])
        except Exception as e:
            print(f"Permission request error: {e}")

    def _init_core(self, dt):
        splash = self.sm.get_screen("splash")
        splash.status_text = "Loading config…"
        self._load_config()
        splash.status_text = "Initialising identity…"
        threading.Thread(target=self._start_rns, daemon=True).start()

    def _load_config(self):
        import json
        cfg_path = os.path.join(APP_DIR, "config.json")
        if os.path.exists(cfg_path):
            try:
                with open(cfg_path) as f:
                    self.config_data = json.load(f)
            except Exception:
                self.config_data = {}
        else:
            self.config_data = {}

    def _save_config(self):
        import json
        cfg_path = os.path.join(APP_DIR, "config.json")
        try:
            with open(cfg_path, "w") as f:
                json.dump(self.config_data, f, indent=2)
        except Exception as e:
            print(f"Config save error: {e}")

    def _start_rns(self):
        try:
            from nano.config import NanoConfig
            from nano.db import NanoDB
            from nano.core import NanoCore

            cfg = NanoConfig(base_dir=APP_DIR)
            # Apply saved settings
            if self.config_data.get("display_name"):
                cfg["display_name"] = self.config_data["display_name"]

            db = NanoDB(os.path.join(APP_DIR, "nano.db"))
            self.db = db

            # Build RNS config with RNode BT if configured
            self._write_rns_config(cfg)

            core = NanoCore(cfg, db)
            core.on_message(self._on_message)
            core.on_delivery_status(self._on_status)
            core.start()
            self.core = core

            Clock.schedule_once(lambda dt: self._on_rns_ready(), 0)
        except Exception as e:
            err = str(e)
            Clock.schedule_once(lambda dt: self._on_rns_error(err), 0)

    def _write_rns_config(self, cfg):
        """Write ~/.reticulum/config with RNode BT interface if configured."""
        try:
            import RNS
            rns_dir = os.path.join(APP_DIR, "reticulum")
            os.makedirs(rns_dir, exist_ok=True)
            cfg_file = os.path.join(rns_dir, "config")

            bt_addr = self.config_data.get("rnode_bt_addr", "")
            bt_name = self.config_data.get("rnode_bt_name", "RNode")
            freq    = float(self.config_data.get("rnode_freq", 915.0)) * 1e6
            bw      = int(self.config_data.get("rnode_bw", 125)) * 1000
            sf      = int(self.config_data.get("rnode_sf", 8))
            txp     = int(self.config_data.get("rnode_txp", 17))

            if bt_addr:
                iface_section = f"""
[interface:RNode BT]
  type = RNodeInterface
  interface_enabled = True
  target_device = {bt_addr}
  target_device_name = {bt_name}
  frequency = {int(freq)}
  bandwidth = {bw}
  txpower = {txp}
  spreadingfactor = {sf}
  codingrate = 5
"""
            else:
                iface_section = """
[interface:AutoInterface]
  type = AutoInterface
  interface_enabled = True
"""
            config_text = f"""[reticulum]
  enable_transport = False
  share_instance = True
  rns_path = {rns_dir}
{iface_section}
"""
            with open(cfg_file, "w") as f:
                f.write(config_text)
            # Point RNS to our config
            os.environ["RNS_CONFIG_DIR"] = rns_dir
        except Exception as e:
            print(f"RNS config write error: {e}")

    def _restart_rns(self):
        try:
            if self.core:
                self.core.stop()
                self.core = None
            time.sleep(1)
            self._start_rns()
        except Exception as e:
            print(f"Restart RNS error: {e}")

    def _on_rns_ready(self):
        splash = self.sm.get_screen("splash")
        splash.status_text = "Connected!"
        Clock.schedule_once(lambda dt: self._goto_conversations(), 0.8)

    def _on_rns_error(self, err):
        splash = self.sm.get_screen("splash")
        splash.status_text = f"RNS error: {err[:60]}"
        # Still go to conversations — user can configure RNode
        Clock.schedule_once(lambda dt: self._goto_conversations(), 2)

    def _goto_conversations(self):
        self.sm.transition = SlideTransition(direction="left")
        self.sm.current = "conversations"
        # Schedule periodic refresh
        Clock.schedule_interval(self._periodic_refresh, 5)

    def _periodic_refresh(self, dt):
        if self.sm.current == "conversations":
            self.sm.get_screen("conversations").refresh()
        elif self.sm.current == "messages":
            self.sm.get_screen("messages").refresh()

    def _on_message(self, source_hash, display_name, content,
                    fields, timestamp, msg_hash, has_image, **kw):
        Clock.schedule_once(lambda dt: self._handle_incoming(
            source_hash, display_name, content), 0)

    def _handle_incoming(self, source_hash, display_name, content):
        src = source_hash.strip("<>")
        if self.sm.current == "messages" and self.current_peer == src:
            self.sm.get_screen("messages").refresh()
        elif self.sm.current == "conversations":
            self.sm.get_screen("conversations").refresh()

    def _on_status(self, msg_id, status, **kw):
        if self.sm.current == "messages":
            Clock.schedule_once(
                lambda dt: self.sm.get_screen("messages").refresh(), 0)

    def on_pause(self):
        return True  # Keep running in background

    def on_resume(self):
        pass


if __name__ == "__main__":
    NanoSidebandApp().run()
