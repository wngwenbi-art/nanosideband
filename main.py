"""
main.py — NanoSideband Android/Desktop entry point.
All Kivy UI code lives here so buildozer can find it without submodule imports.
"""
import os, sys, threading, time

ANDROID = False
try:
    from android import mActivity  # noqa
    ANDROID = True
except ImportError:
    pass

os.environ.setdefault("KIVY_NO_ENV_CONFIG", "1")
from kivy.config import Config
Config.set("graphics", "resizable", "1")
Config.set("kivy", "log_level", "warning")

from kivy.app import App
from kivy.clock import Clock
from kivy.lang import Builder
from kivy.uix.screenmanager import ScreenManager, Screen, SlideTransition
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.textinput import TextInput
from kivy.uix.popup import Popup
from kivy.uix.widget import Widget
from kivy.uix.spinner import Spinner
from kivy.properties import StringProperty, ListProperty
from kivy.metrics import dp
from kivy.graphics import Color as KColor, RoundedRectangle

if ANDROID:
    from android.storage import app_storage_path  # type: ignore
    APP_DIR = app_storage_path()
else:
    APP_DIR = os.path.join(os.path.expanduser("~"), ".nanosideband")
os.makedirs(APP_DIR, exist_ok=True)

# Add project root to path so nano.* imports work
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# ── KV ────────────────────────────────────────────────────────────────────────
KV = """
#:import dp kivy.metrics.dp

<NsBtn@Button>:
    background_color: 0.91,0.27,0.37,1
    background_normal: ''
    color: 1,1,1,1
    font_size: dp(15)
    bold: True
    size_hint_y: None
    height: dp(46)

<Ns2Btn@Button>:
    background_color: 0.09,0.2,0.38,1
    background_normal: ''
    color: 0.88,0.88,0.88,1
    font_size: dp(15)
    size_hint_y: None
    height: dp(46)

<NsInp@TextInput>:
    background_color: 0.06,0.19,0.38,1
    foreground_color: 0.88,0.88,0.88,1
    cursor_color: 0.91,0.27,0.37,1
    font_size: dp(15)
    padding: [dp(12),dp(10)]
    size_hint_y: None
    height: dp(46)
    multiline: False

<SplashScreen>:
    name: 'splash'
    canvas.before:
        Color:
            rgba: 0.1,0.1,0.18,1
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
            color: 0.91,0.27,0.37,1
            size_hint_y: None
            height: dp(50)
        Label:
            text: root.status_text
            font_size: dp(14)
            color: 0.63,0.63,0.69,1
            size_hint_y: None
            height: dp(30)
        Widget:
            size_hint_y: 0.4

<ConvScreen>:
    name: 'conversations'
    canvas.before:
        Color:
            rgba: 0.1,0.1,0.18,1
        Rectangle:
            pos: self.pos
            size: self.size
    BoxLayout:
        orientation: 'vertical'
        BoxLayout:
            size_hint_y: None
            height: dp(56)
            padding: dp(10),dp(6)
            spacing: dp(6)
            canvas.before:
                Color:
                    rgba: 0.06,0.13,0.24,1
                Rectangle:
                    pos: self.pos
                    size: self.size
            Label:
                text: 'NanoSideband'
                font_size: dp(20)
                bold: True
                color: 0.91,0.27,0.37,1
                halign: 'left'
                valign: 'middle'
                text_size: self.size
            Ns2Btn:
                text: '[size=20]📡[/size]'
                markup: True
                size_hint_x: None
                width: dp(48)
                on_release: root.do_announce()
            Ns2Btn:
                text: '[size=20]⚙[/size]'
                markup: True
                size_hint_x: None
                width: dp(48)
                on_release: root.goto_settings()
            Ns2Btn:
                text: '[size=20]📻[/size]'
                markup: True
                size_hint_x: None
                width: dp(48)
                on_release: root.goto_rnode()
        ScrollView:
            do_scroll_x: False
            BoxLayout:
                id: conv_list
                orientation: 'vertical'
                spacing: dp(1)
                size_hint_y: None
                height: self.minimum_height
                padding: 0,0,0,dp(70)
        NsBtn:
            size_hint_y: None
            height: dp(48)
            text: '＋  New Conversation'
            on_release: root.goto_new()

<MsgScreen>:
    name: 'messages'
    canvas.before:
        Color:
            rgba: 0.1,0.1,0.18,1
        Rectangle:
            pos: self.pos
            size: self.size
    BoxLayout:
        orientation: 'vertical'
        BoxLayout:
            size_hint_y: None
            height: dp(56)
            padding: dp(6),dp(6)
            spacing: dp(6)
            canvas.before:
                Color:
                    rgba: 0.06,0.13,0.24,1
                Rectangle:
                    pos: self.pos
                    size: self.size
            Ns2Btn:
                text: '←'
                size_hint_x: None
                width: dp(46)
                on_release: root.go_back()
            Label:
                text: root.peer_name
                font_size: dp(16)
                bold: True
                color: 0.88,0.88,0.88,1
                halign: 'left'
                valign: 'middle'
                text_size: self.size
        ScrollView:
            id: msg_scroll
            do_scroll_x: False
            BoxLayout:
                id: msg_list
                orientation: 'vertical'
                spacing: dp(6)
                padding: dp(10),dp(10)
                size_hint_y: None
                height: self.minimum_height
        BoxLayout:
            size_hint_y: None
            height: dp(56)
            padding: dp(8),dp(6)
            spacing: dp(6)
            canvas.before:
                Color:
                    rgba: 0.06,0.13,0.24,1
                Rectangle:
                    pos: self.pos
                    size: self.size
            NsInp:
                id: compose
                hint_text: 'Type a message…'
                on_text_validate: root.send_msg()
            NsBtn:
                text: 'Send'
                size_hint_x: None
                width: dp(80)
                height: dp(42)
                on_release: root.send_msg()

<NewConvScreen>:
    name: 'new_conv'
    canvas.before:
        Color:
            rgba: 0.1,0.1,0.18,1
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
            Ns2Btn:
                text: '←'
                size_hint_x: None
                width: dp(46)
                on_release: root.go_back()
            Label:
                text: 'New Conversation'
                font_size: dp(18)
                bold: True
                color: 0.88,0.88,0.88,1
        Label:
            text: 'Destination hash (32 hex chars):'
            font_size: dp(13)
            color: 0.63,0.63,0.69,1
            size_hint_y: None
            height: dp(24)
        NsInp:
            id: hash_inp
            hint_text: 'e.g. a3f2b1c4d5e6…'
        Label:
            id: err_lbl
            text: ''
            color: 0.91,0.27,0.37,1
            font_size: dp(13)
            size_hint_y: None
            height: dp(24)
        NsBtn:
            text: 'Start Conversation'
            on_release: root.start_conv()
        Widget:

<RNodeScreen>:
    name: 'rnode'
    canvas.before:
        Color:
            rgba: 0.1,0.1,0.18,1
        Rectangle:
            pos: self.pos
            size: self.size
    ScrollView:
        BoxLayout:
            orientation: 'vertical'
            padding: dp(16)
            spacing: dp(12)
            size_hint_y: None
            height: self.minimum_height
            BoxLayout:
                size_hint_y: None
                height: dp(56)
                Ns2Btn:
                    text: '←'
                    size_hint_x: None
                    width: dp(46)
                    on_release: root.go_back()
                Label:
                    text: 'RNode Bluetooth Setup'
                    font_size: dp(18)
                    bold: True
                    color: 0.88,0.88,0.88,1
            Label:
                text: 'Paired Bluetooth Devices:'
                font_size: dp(13)
                color: 0.63,0.63,0.69,1
                size_hint_y: None
                height: dp(24)
            Spinner:
                id: bt_spinner
                text: 'Select device…'
                values: root.bt_devices
                size_hint_y: None
                height: dp(46)
                background_color: 0.06,0.19,0.38,1
                color: 0.88,0.88,0.88,1
            Ns2Btn:
                text: '🔄 Refresh devices'
                on_release: root.refresh_devices()
            Label:
                text: 'Frequency (MHz):'
                color: 0.88,0.88,0.88,1
                font_size: dp(13)
                size_hint_y: None
                height: dp(24)
            NsInp:
                id: freq_inp
                text: root.rnode_freq
                hint_text: '915.0'
            Label:
                text: 'Bandwidth (kHz):'
                color: 0.88,0.88,0.88,1
                font_size: dp(13)
                size_hint_y: None
                height: dp(24)
            NsInp:
                id: bw_inp
                text: root.rnode_bw
                hint_text: '125'
            Label:
                text: 'Spreading Factor:'
                color: 0.88,0.88,0.88,1
                font_size: dp(13)
                size_hint_y: None
                height: dp(24)
            NsInp:
                id: sf_inp
                text: root.rnode_sf
                hint_text: '8'
            Label:
                text: 'TX Power (dBm):'
                color: 0.88,0.88,0.88,1
                font_size: dp(13)
                size_hint_y: None
                height: dp(24)
            NsInp:
                id: txp_inp
                text: root.rnode_txp
                hint_text: '17'
            Label:
                id: rnode_status
                text: root.rnode_status
                color: 0.91,0.27,0.37,1
                font_size: dp(13)
                size_hint_y: None
                height: dp(28)
            BoxLayout:
                size_hint_y: None
                height: dp(46)
                spacing: dp(8)
                Ns2Btn:
                    text: 'Test Connection'
                    on_release: root.test_connection()
                NsBtn:
                    text: 'Save & Connect'
                    on_release: root.save_and_connect()

<SettingsScreen>:
    name: 'settings'
    canvas.before:
        Color:
            rgba: 0.1,0.1,0.18,1
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
            Ns2Btn:
                text: '←'
                size_hint_x: None
                width: dp(46)
                on_release: root.go_back()
            Label:
                text: 'Settings'
                font_size: dp(18)
                bold: True
                color: 0.88,0.88,0.88,1
        Label:
            text: 'Display Name:'
            font_size: dp(13)
            color: 0.63,0.63,0.69,1
            size_hint_y: None
            height: dp(24)
        NsInp:
            id: name_inp
            text: root.display_name
            hint_text: 'Your name on the network'
        Label:
            text: 'Identity Hash:'
            font_size: dp(13)
            color: 0.63,0.63,0.69,1
            size_hint_y: None
            height: dp(24)
        Label:
            text: root.identity_hash
            font_size: dp(11)
            color: 0.63,0.63,0.69,1
            size_hint_y: None
            height: dp(30)
        NsBtn:
            text: 'Save'
            on_release: root.save_settings()
        Widget:
"""

# ── Screens ───────────────────────────────────────────────────────────────────

class SplashScreen(Screen):
    status_text = StringProperty("Starting…")


class ConvScreen(Screen):
    def on_enter(self):
        Clock.schedule_once(lambda dt: self.refresh(), 0.1)

    def refresh(self):
        app = App.get_running_app()
        box = self.ids.conv_list
        box.clear_widgets()
        convs = app.db.list_conversations() if app.db else []
        contacts = {c["dest_hash"]: c for c in (app.db.list_contacts() if app.db else [])}
        if not convs:
            box.add_widget(Label(
                text="No conversations yet.\nPress  ＋  to start one.",
                font_size=dp(14), color=(0.63,0.63,0.69,1),
                halign="center", size_hint_y=None, height=dp(80)))
            return
        convs.sort(key=lambda c: c.get("last_ts",0), reverse=True)
        for cv in convs:
            peer = cv["peer"]
            name = contacts.get(peer,{}).get("display_name") or peer[:12]+"…"
            unread = cv.get("unread",0)
            badge = f"  [{unread}]" if unread else ""
            btn = Button(
                text=f"{name}{badge}\n[size=11sp][color=888888]{peer[:20]}…[/color][/size]",
                markup=True, size_hint_y=None, height=dp(64),
                background_normal="", background_color=(0.09,0.2,0.38,1),
                halign="left", valign="middle", padding=(dp(16),0))
            btn.bind(on_release=lambda b,p=peer,n=name: self.open_conv(p,n))
            box.add_widget(btn)

    def open_conv(self, peer, name):
        app = App.get_running_app()
        app.current_peer = peer
        app.current_peer_name = name
        if app.db: app.db.mark_read(peer)
        app.sm.transition = SlideTransition(direction="left")
        app.sm.current = "messages"

    def do_announce(self):
        app = App.get_running_app()
        if app.core: app.core.announce()
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
        p = Popup(title="", content=Label(text=msg, color=(0.88,0.88,0.88,1)),
                  size_hint=(0.6,None), height=dp(80))
        p.open(); Clock.schedule_once(lambda dt: p.dismiss(), 2)


class MsgScreen(Screen):
    peer_name = StringProperty("Conversation")

    def on_enter(self):
        app = App.get_running_app()
        self.peer_name = app.current_peer_name or (app.current_peer[:12]+"…")
        Clock.schedule_once(lambda dt: self.refresh(), 0.1)

    def refresh(self):
        app = App.get_running_app()
        if not app.db or not app.current_peer: return
        box = self.ids.msg_list
        box.clear_widgets()
        msgs = app.db.list_messages(app.current_peer, limit=80)
        msgs.reverse()
        state_icons = {"pending":"○","sent":"◎","delivered":"●","failed":"✗"}
        for m in msgs:
            direction = m["direction"]
            content = m.get("content") or ""
            ts = time.strftime("%H:%M", time.localtime(m["timestamp"]))
            icon = state_icons.get(m.get("state",""),"") if direction=="out" else ""
            img = " [📷]" if m.get("has_image") else ""
            txt = f"{content}{img}\n[size=11sp][color=888888]{ts} {icon}[/color][/size]"
            bg = (0.12,0.18,0.29,1) if direction=="in" else (0.1,0.23,0.16,1)
            lbl = Label(text=txt, markup=True, size_hint_y=None,
                        size_hint_x=0.8, halign="left" if direction=="in" else "right",
                        valign="top", color=(0.88,0.88,0.88,1), font_size=dp(14),
                        padding=(dp(12),dp(8)))
            lbl.bind(width=lambda l,w: setattr(l,"text_size",(w,None)))
            lbl.bind(texture_size=lambda l,s: setattr(l,"height",s[1]+dp(20)))
            with lbl.canvas.before:
                KColor(rgba=bg)
                lbl._bg = RoundedRectangle(radius=[dp(10)], pos=lbl.pos, size=lbl.size)
            lbl.bind(pos=lambda l,v: setattr(l._bg,"pos",v))
            lbl.bind(size=lambda l,v: setattr(l._bg,"size",v))
            row = BoxLayout(size_hint_y=None, height=dp(60))
            row.bind(minimum_height=row.setter("height"))
            if direction=="out": row.add_widget(Widget())
            row.add_widget(lbl)
            if direction=="in": row.add_widget(Widget())
            box.add_widget(row)
        Clock.schedule_once(lambda dt: setattr(self.ids.msg_scroll,"scroll_y",0), 0.1)

    def send_msg(self):
        app = App.get_running_app()
        inp = self.ids.compose
        text = inp.text.strip()
        if not text or not app.current_peer: return
        if not app.core:
            self._toast("Not connected"); return
        inp.text = ""
        threading.Thread(target=app.core.send_text,
                         args=(app.current_peer, text), daemon=True).start()
        Clock.schedule_once(lambda dt: self.refresh(), 0.4)

    def go_back(self):
        app = App.get_running_app()
        app.sm.transition = SlideTransition(direction="right")
        app.sm.current = "conversations"

    def _toast(self, msg):
        p = Popup(title="", content=Label(text=msg, color=(0.88,0.88,0.88,1)),
                  size_hint=(0.6,None), height=dp(80))
        p.open(); Clock.schedule_once(lambda dt: p.dismiss(), 2)


class NewConvScreen(Screen):
    def start_conv(self):
        app = App.get_running_app()
        h = self.ids.hash_inp.text.strip().lower().replace("<","").replace(">","")
        if len(h) != 32:
            self.ids.err_lbl.text = "Must be exactly 32 hex chars"; return
        if app.db: app.db.upsert_contact(h)
        app.current_peer = h
        app.current_peer_name = h[:12]+"…"
        app.sm.transition = SlideTransition(direction="left")
        app.sm.current = "messages"
        self.ids.hash_inp.text = ""; self.ids.err_lbl.text = ""

    def go_back(self):
        app = App.get_running_app()
        app.sm.transition = SlideTransition(direction="right")
        app.sm.current = "conversations"


class RNodeScreen(Screen):
    bt_devices   = ListProperty([])
    rnode_freq   = StringProperty("915.0")
    rnode_bw     = StringProperty("125")
    rnode_sf     = StringProperty("8")
    rnode_txp    = StringProperty("17")
    rnode_status = StringProperty("")

    def on_enter(self):
        app = App.get_running_app()
        cfg = app.config_data
        self.rnode_freq = str(cfg.get("rnode_freq","915.0"))
        self.rnode_bw   = str(cfg.get("rnode_bw","125"))
        self.rnode_sf   = str(cfg.get("rnode_sf","8"))
        self.rnode_txp  = str(cfg.get("rnode_txp","17"))
        Clock.schedule_once(lambda dt: self.refresh_devices(), 0.2)

    def refresh_devices(self):
        self.rnode_status = "Scanning for paired devices…"
        threading.Thread(target=self._scan_bt, daemon=True).start()

    def _scan_bt(self):
        devices = []
        try:
            if ANDROID:
                from jnius import autoclass  # type: ignore
                BTA = autoclass("android.bluetooth.BluetoothAdapter")
                adapter = BTA.getDefaultAdapter()
                if adapter:
                    for d in adapter.getBondedDevices().toArray():
                        devices.append(f"{d.getName()} [{d.getAddress()}]")
            else:
                try:
                    import bluetooth  # type: ignore
                    for addr, name in bluetooth.discover_devices(duration=4, lookup_names=True):
                        devices.append(f"{name} [{addr}]")
                except Exception:
                    devices = ["(bluetooth scan unavailable on desktop)"]
        except Exception as e:
            devices = [f"Error: {e}"]
        Clock.schedule_once(lambda dt: self._set_devices(devices), 0)

    def _set_devices(self, devices):
        self.bt_devices = devices or ["(no paired devices found)"]
        self.rnode_status = f"Found {len(devices)} device(s). Select one above."

    def test_connection(self):
        sel = self.ids.bt_spinner.text
        if not sel or "Select" in sel:
            self.rnode_status = "Select a device first"; return
        self.rnode_status = "Testing connection…"
        threading.Thread(target=self._do_test, args=(sel,), daemon=True).start()

    def _do_test(self, device_str):
        try:
            addr = device_str.split("[")[-1].rstrip("]")
            if ANDROID:
                from jnius import autoclass  # type: ignore
                BTA = autoclass("android.bluetooth.BluetoothAdapter")
                UUID = autoclass("java.util.UUID")
                SPP = "00001101-0000-1000-8000-00805F9B34FB"
                dev = BTA.getDefaultAdapter().getRemoteDevice(addr)
                sock = dev.createInsecureRfcommSocketToServiceRecord(UUID.fromString(SPP))
                sock.connect()
                sock.getOutputStream().write([0x3A]); time.sleep(0.3)
                inp = sock.getInputStream()
                resp = bytearray([inp.read() for _ in range(inp.available())])
                sock.close()
                msg = f"Connected! {len(resp)} bytes received ✓" if resp else "Connected (no response — may still work)"
            else:
                msg = f"Desktop test: would connect to {addr}"
        except Exception as e:
            msg = f"Failed: {e}"
        Clock.schedule_once(lambda dt: setattr(self,"rnode_status",msg), 0)

    def save_and_connect(self):
        sel = self.ids.bt_spinner.text
        if not sel or "Select" in sel:
            self.rnode_status = "Select a device first"; return
        addr = sel.split("[")[-1].rstrip("]")
        app = App.get_running_app()
        app.config_data.update({
            "rnode_bt_addr": addr,
            "rnode_bt_name": sel.split(" [")[0],
            "rnode_freq": self.ids.freq_inp.text,
            "rnode_bw":   self.ids.bw_inp.text,
            "rnode_sf":   self.ids.sf_inp.text,
            "rnode_txp":  self.ids.txp_inp.text,
        })
        app._save_config()
        self.rnode_status = f"Saved! Reconnecting to {addr}…"
        threading.Thread(target=app._restart_rns, daemon=True).start()
        Clock.schedule_once(lambda dt: self.go_back(), 2)

    def go_back(self):
        app = App.get_running_app()
        app.sm.transition = SlideTransition(direction="right")
        app.sm.current = "conversations"


class SettingsScreen(Screen):
    display_name  = StringProperty("")
    identity_hash = StringProperty("")

    def on_enter(self):
        app = App.get_running_app()
        self.display_name  = app.config_data.get("display_name","")
        self.identity_hash = (app.core.identity_hash if app.core else "Not initialized") or ""

    def save_settings(self):
        app = App.get_running_app()
        app.config_data["display_name"] = self.ids.name_inp.text.strip()
        app._save_config()
        if app.core: app.core.config["display_name"] = app.config_data["display_name"]
        p = Popup(title="", content=Label(text="Saved!", color=(0.88,0.88,0.88,1)),
                  size_hint=(0.4,None), height=dp(70))
        p.open(); Clock.schedule_once(lambda dt: p.dismiss(), 1.5)

    def go_back(self):
        app = App.get_running_app()
        app.sm.transition = SlideTransition(direction="right")
        app.sm.current = "conversations"


# ── App ───────────────────────────────────────────────────────────────────────

class NanoSidebandApp(App):
    title = "NanoSideband"

    def __init__(self, **kw):
        super().__init__(**kw)
        self.core = None; self.db = None; self.sm = None
        self.current_peer = ""; self.current_peer_name = ""
        self.config_data = {}

    def build(self):
        try:
            Builder.load_string(KV)
        except Exception as e:
            from kivy.uix.label import Label
            return Label(text=f'KV Error: {e}', color=(1,0,0,1))
        self.sm = ScreenManager()
        for S in (SplashScreen, ConvScreen, MsgScreen, NewConvScreen,
                  RNodeScreen, SettingsScreen):
            self.sm.add_widget(S())
        self.sm.current = "splash"
        if ANDROID: self._request_permissions()
        Clock.schedule_once(self._init_core, 0.5)
        return self.sm

    def _request_permissions(self):
        try:
            from android.permissions import request_permissions, Permission  # type: ignore
            request_permissions([
                Permission.BLUETOOTH, Permission.BLUETOOTH_ADMIN,
                Permission.BLUETOOTH_CONNECT, Permission.BLUETOOTH_SCAN,
                Permission.ACCESS_FINE_LOCATION,
                Permission.READ_EXTERNAL_STORAGE, Permission.WRITE_EXTERNAL_STORAGE,
            ])
        except Exception as e:
            print(f"Permissions: {e}")

    def _init_core(self, dt):
        self._set_splash("Loading config…")
        self._load_config()
        self._set_splash("Initialising RNS…")
        threading.Thread(target=self._start_rns, daemon=True).start()

    def _set_splash(self, msg):
        self.sm.get_screen("splash").status_text = msg

    def _load_config(self):
        import json
        p = os.path.join(APP_DIR, "config.json")
        try:
            with open(p) as f: self.config_data = json.load(f)
        except Exception:
            self.config_data = {}

    def _save_config(self):
        import json
        p = os.path.join(APP_DIR, "config.json")
        try:
            with open(p,"w") as f: json.dump(self.config_data, f, indent=2)
        except Exception as e:
            print(f"Config save: {e}")

    def _write_rns_config(self):
        rns_dir = os.path.join(APP_DIR, "reticulum")
        os.makedirs(rns_dir, exist_ok=True)
        bt_addr = self.config_data.get("rnode_bt_addr","")
        if bt_addr:
            freq = float(self.config_data.get("rnode_freq",915.0))*1e6
            bw   = int(self.config_data.get("rnode_bw",125))*1000
            sf   = int(self.config_data.get("rnode_sf",8))
            txp  = int(self.config_data.get("rnode_txp",17))
            name = self.config_data.get("rnode_bt_name","RNode")
            iface = f"""
[interface:RNode BT]
  type = RNodeInterface
  interface_enabled = True
  target_device = {bt_addr}
  target_device_name = {name}
  frequency = {int(freq)}
  bandwidth = {bw}
  txpower = {txp}
  spreadingfactor = {sf}
  codingrate = 5
"""
        else:
            iface = """
[interface:AutoInterface]
  type = AutoInterface
  interface_enabled = True
"""
        cfg_text = f"[reticulum]\n  enable_transport = False\n  share_instance = True\n  rns_path = {rns_dir}\n{iface}\n"
        with open(os.path.join(rns_dir,"config"),"w") as f: f.write(cfg_text)
        os.environ["RNS_CONFIG_DIR"] = rns_dir

    def _start_rns(self):
        try:
            self._write_rns_config()
            from nano.config import NanoConfig
            from nano.core import NanoCore
            cfg = NanoConfig(base_dir=APP_DIR)
            if self.config_data.get("display_name"):
                cfg["display_name"] = self.config_data["display_name"]
            core = NanoCore(cfg)
            core.on_message(self._on_message)
            core.on_delivery_status(self._on_status)
            core.start()          # opens db inside
            self.core = core
            self.db   = core.db   # share the already-open db instance
            Clock.schedule_once(lambda dt: self._rns_ready(), 0)
        except Exception as e:
            err = str(e)
            Clock.schedule_once(lambda dt: self._rns_error(err), 0)

    def _restart_rns(self):
        try:
            if self.core: self.core.stop(); self.core = None
            time.sleep(1); self._start_rns()
        except Exception as e:
            print(f"Restart: {e}")

    def _rns_ready(self):
        self._set_splash("Connected!")
        Clock.schedule_once(lambda dt: self._goto_convs(), 0.8)

    def _rns_error(self, err):
        self._set_splash(f"RNS: {err[:50]}")
        Clock.schedule_once(lambda dt: self._goto_convs(), 2)

    def _goto_convs(self):
        self.sm.transition = SlideTransition(direction="left")
        self.sm.current = "conversations"
        Clock.schedule_interval(self._tick, 5)

    def _tick(self, dt):
        cur = self.sm.current
        if cur == "conversations":
            self.sm.get_screen("conversations").refresh()
        elif cur == "messages":
            self.sm.get_screen("messages").refresh()

    def _on_message(self, source_hash, display_name, content,
                    fields, timestamp, msg_hash, has_image, **kw):
        Clock.schedule_once(lambda dt: self._handle_msg(source_hash), 0)

    def _handle_msg(self, source_hash):
        src = source_hash.strip("<>")
        if self.sm.current == "messages" and self.current_peer == src:
            self.sm.get_screen("messages").refresh()
        elif self.sm.current == "conversations":
            self.sm.get_screen("conversations").refresh()

    def _on_status(self, msg_id, status, **kw):
        if self.sm.current == "messages":
            Clock.schedule_once(lambda dt: self.sm.get_screen("messages").refresh(), 0)

    def on_pause(self): return True
    def on_resume(self): pass


if __name__ == "__main__":
    NanoSidebandApp().run()
