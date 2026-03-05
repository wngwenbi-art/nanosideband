"""
nano/rnode_bt.py
RFCOMM RNode interface — mirrors the real RNS RNodeInterface protocol exactly.

Protocol reference: RNS/Interfaces/RNodeInterface.py
The real RNodeInterface does:
  1. Open serial/rfcomm
  2. Send detect request: C0 08 73 C0
  3. Wait for detect response: C0 08 46 C0 (or with header bytes)
  4. Send radio config (freq, bw, sf, txp, cr)
  5. Send radio ON: C0 06 01 C0
  6. Read loop: parse KISS frames, call processIncoming() for CMD_DATA frames

KISS encoding mirrors RNS source exactly.
"""

import threading
import time
import logging
import struct

log = logging.getLogger(__name__)

# ── KISS constants (from RNS source) ─────────────────────────────────────────
FEND  = 0xC0
FESC  = 0xDB
TFEND = 0xDC
TFESC = 0xDD

CMD_DATA         = 0x00
CMD_FREQUENCY    = 0x01
CMD_BANDWIDTH    = 0x02
CMD_TXPOWER      = 0x03
CMD_SF           = 0x04
CMD_CR           = 0x05
CMD_RADIO_STATE  = 0x06
CMD_RADIO_LOCK   = 0x07
CMD_DETECT       = 0x08
CMD_LEAVE        = 0x0A
CMD_READY        = 0x0F
CMD_STAT_RX      = 0x21
CMD_STAT_TX      = 0x22
CMD_STAT_RSSI    = 0x23
CMD_STAT_SNR     = 0x24
CMD_FW_VERSION   = 0x50
CMD_PLATFORM     = 0x48
CMD_MCU          = 0x49

DETECT_REQ       = 0x73
DETECT_RESP      = 0x46
RADIO_STATE_ON   = 0x01
RADIO_STATE_OFF  = 0x00

SPP_UUID = "00001101-0000-1000-8000-00805F9B34FB"

CONNECT_TIMEOUT  = 8.0   # seconds to wait for detect response
CONFIG_DELAY     = 0.05  # seconds between config commands


# ── KISS framing (exact match to RNS source) ─────────────────────────────────

def kiss_escape(data: bytes) -> bytes:
    out = bytearray()
    for b in data:
        if b == FEND:
            out += bytes([FESC, TFEND])
        elif b == FESC:
            out += bytes([FESC, TFESC])
        else:
            out.append(b)
    return bytes(out)

def kiss_unescape(data: bytes) -> bytes:
    out = bytearray()
    i = 0
    while i < len(data):
        b = data[i]
        if b == FESC and i + 1 < len(data):
            nxt = data[i+1]
            if nxt == TFEND:
                out.append(FEND)
            elif nxt == TFESC:
                out.append(FESC)
            i += 2
        else:
            out.append(b)
            i += 1
    return bytes(out)

def kiss_cmd(cmd: int, data: bytes = b'') -> bytes:
    """Build a complete KISS frame: FEND cmd escaped_data FEND"""
    return bytes([FEND, cmd]) + kiss_escape(data) + bytes([FEND])


# ── Low-level RFCOMM writer ───────────────────────────────────────────────────

def write_all(stream, data: bytes):
    """Write bytes to a Java OutputStream. Use write(byte[]) for efficiency."""
    arr = bytearray(data)
    stream.write(arr, 0, len(arr))
    stream.flush()


# ── RNodeBTInterface ─────────────────────────────────────────────────────────

class RNodeBTInterface:
    """
    RFCOMM connection to an RNode. Speaks KISS exactly like the real
    RNS RNodeInterface. Call connect() then use send()/on_packet.
    """

    def __init__(self, bt_addr: str, frequency: int, bandwidth: int,
                 spreading_factor: int, tx_power: int, coding_rate: int = 5,
                 on_packet=None):
        self.bt_addr          = bt_addr
        self.frequency        = frequency
        self.bandwidth        = bandwidth
        self.spreading_factor = spreading_factor
        self.tx_power         = tx_power
        self.coding_rate      = coding_rate
        self.on_packet        = on_packet

        self._sock       = None
        self._out        = None
        self._in         = None
        self._running    = False
        self._lock       = threading.Lock()
        self.connected   = False
        self.detected    = False
        self.status      = "Disconnected"

    # ── Connect ──────────────────────────────────────────────────────────────

    def connect(self) -> bool:
        try:
            self.status = "Connecting…"
            print(f"[RNODE_BT] connect() → {self.bt_addr}")

            from jnius import autoclass  # type: ignore
            BTA  = autoclass("android.bluetooth.BluetoothAdapter")
            UUID = autoclass("java.util.UUID")

            adapter = BTA.getDefaultAdapter()
            if not adapter:
                self.status = "No Bluetooth adapter"
                return False
            if not adapter.isEnabled():
                self.status = "Bluetooth not enabled"
                return False

            adapter.cancelDiscovery()
            dev  = adapter.getRemoteDevice(self.bt_addr)
            sock = dev.createInsecureRfcommSocketToServiceRecord(
                UUID.fromString(SPP_UUID))

            print(f"[RNODE_BT] socket created, connecting…")
            sock.connect()
            print(f"[RNODE_BT] socket connected OK")

            self._sock = sock
            self._out  = sock.getOutputStream()
            self._in   = sock.getInputStream()
            self.connected = True

            # ── Detect handshake ─────────────────────────────────────────────
            # Send: C0 08 73 C0  (FEND CMD_DETECT DETECT_REQ FEND)
            # Expect: C0 08 46 C0  (FEND CMD_DETECT DETECT_RESP FEND)
            self.status = "Detecting RNode…"
            write_all(self._out, kiss_cmd(CMD_DETECT, bytes([DETECT_REQ])))

            detected = self._wait_for_detect(timeout=CONNECT_TIMEOUT)
            if not detected:
                self.status = "RNode not detected (no detect response)"
                print("[RNODE_BT] detect timeout")
                sock.close()
                self.connected = False
                return False

            self.detected = True
            print("[RNODE_BT] RNode detected!")

            # ── Radio config ─────────────────────────────────────────────────
            self.status = "Configuring radio…"
            self._configure()

            # ── Start read loop ──────────────────────────────────────────────
            self._running = True
            threading.Thread(target=self._read_loop, daemon=True,
                             name="rnode-rx").start()

            self.status = "RNode active"
            print(f"[RNODE_BT] online: {self.frequency}Hz BW={self.bandwidth} "
                  f"SF={self.spreading_factor} TXP={self.tx_power}")
            return True

        except Exception as e:
            self.status = f"Connect failed: {e}"
            print(f"[RNODE_BT] connect FAILED: {e}")
            import traceback; traceback.print_exc()
            self.connected = False
            return False

    def _wait_for_detect(self, timeout: float) -> bool:
        """Read raw bytes until we see CMD_DETECT + DETECT_RESP or timeout."""
        deadline = time.time() + timeout
        buf = bytearray()
        while time.time() < deadline:
            try:
                avail = self._in.available()
                if avail > 0:
                    chunk = bytearray(avail)
                    self._in.read(chunk, 0, avail)
                    buf += chunk
                    print(f"[RNODE_BT] detect rx: {buf.hex()}")
                    # Look for detect response in buffer
                    for i in range(len(buf) - 2):
                        if buf[i] == CMD_DETECT and buf[i+1] == DETECT_RESP:
                            return True
                        # Also accept: FEND CMD_DETECT DETECT_RESP FEND
                        if (buf[i] == FEND and i+2 < len(buf) and
                                buf[i+1] == CMD_DETECT and buf[i+2] == DETECT_RESP):
                            return True
                else:
                    time.sleep(0.05)
            except Exception as e:
                print(f"[RNODE_BT] detect read error: {e}")
                return False
        return False

    # ── Config ───────────────────────────────────────────────────────────────

    def _configure(self):
        """Send radio parameters exactly as RNS RNodeInterface does."""
        # Frequency: 4 bytes big-endian
        write_all(self._out, kiss_cmd(CMD_FREQUENCY,
                  struct.pack(">I", self.frequency)))
        time.sleep(CONFIG_DELAY)

        # Bandwidth: 4 bytes big-endian
        write_all(self._out, kiss_cmd(CMD_BANDWIDTH,
                  struct.pack(">I", self.bandwidth)))
        time.sleep(CONFIG_DELAY)

        # TX Power: 1 byte
        write_all(self._out, kiss_cmd(CMD_TXPOWER,
                  bytes([self.tx_power])))
        time.sleep(CONFIG_DELAY)

        # Spreading Factor: 1 byte
        write_all(self._out, kiss_cmd(CMD_SF,
                  bytes([self.spreading_factor])))
        time.sleep(CONFIG_DELAY)

        # Coding Rate: 1 byte
        write_all(self._out, kiss_cmd(CMD_CR,
                  bytes([self.coding_rate])))
        time.sleep(CONFIG_DELAY)

        # Radio ON
        write_all(self._out, kiss_cmd(CMD_RADIO_STATE,
                  bytes([RADIO_STATE_ON])))
        time.sleep(0.2)

    # ── Send ─────────────────────────────────────────────────────────────────

    def send(self, data: bytes):
        if not self.connected:
            raise IOError("Not connected")
        frame = kiss_cmd(CMD_DATA, data)
        with self._lock:
            write_all(self._out, frame)

    # ── Disconnect ───────────────────────────────────────────────────────────

    def disconnect(self):
        self._running  = False
        self.connected = False
        self.detected  = False
        try:
            if self._out:
                # Send radio off before closing
                write_all(self._out, kiss_cmd(CMD_RADIO_STATE,
                          bytes([RADIO_STATE_OFF])))
        except Exception:
            pass
        try:
            if self._sock:
                self._sock.close()
        except Exception:
            pass
        self._sock = self._out = self._in = None
        self.status = "Disconnected"

    # ── Read loop ────────────────────────────────────────────────────────────

    def _read_loop(self):
        """KISS frame parser — mirrors RNS RNodeInterface read loop."""
        buf       = bytearray()
        in_frame  = False
        escape    = False

        while self._running:
            try:
                avail = self._in.available()
                if avail <= 0:
                    time.sleep(0.01)
                    continue

                chunk = bytearray(avail)
                self._in.read(chunk, 0, avail)

                for b in chunk:
                    if b == FEND:
                        if in_frame and len(buf) >= 1:
                            self._dispatch(bytes(buf))
                        buf      = bytearray()
                        in_frame = True
                        escape   = False
                    elif in_frame:
                        if escape:
                            if b == TFEND:
                                buf.append(FEND)
                            elif b == TFESC:
                                buf.append(FESC)
                            escape = False
                        elif b == FESC:
                            escape = True
                        else:
                            buf.append(b)

            except Exception as e:
                if self._running:
                    print(f"[RNODE_BT] read loop error: {e}")
                    self.connected = False
                    self.status = f"Disconnected: {e}"
                break

    def _dispatch(self, frame: bytes):
        if len(frame) < 1:
            return
        cmd  = frame[0]
        data = frame[1:]
        if cmd == CMD_DATA:
            if self.on_packet:
                try:
                    self.on_packet(data)
                except Exception as e:
                    print(f"[RNODE_BT] on_packet error: {e}")
        else:
            print(f"[RNODE_BT] rx cmd 0x{cmd:02x} data={data.hex()}")


# ── RNS Interface wrapper ─────────────────────────────────────────────────────

def make_rns_interface(bt_addr, frequency, bandwidth,
                       spreading_factor, tx_power, coding_rate=5):
    try:
        import RNS
        from RNS.Interfaces.Interface import Interface

        class AndroidRNodeInterface(Interface):
            BITRATE_MINIMUM = 1
            HW_MTU = 508

            def __init__(self):
                super().__init__()
                self.name    = f"RNodeBT[{bt_addr}]"
                self.rxb     = 0
                self.txb     = 0
                self.online  = False
                self.bitrate = 1200

                self._bt = RNodeBTInterface(
                    bt_addr=bt_addr,
                    frequency=frequency,
                    bandwidth=bandwidth,
                    spreading_factor=spreading_factor,
                    tx_power=tx_power,
                    coding_rate=coding_rate,
                    on_packet=self._rx,
                )

            def start(self):
                ok = self._bt.connect()
                self.online = ok
                return ok

            def stop(self):
                self._bt.disconnect()
                self.online = False

            @property
            def status(self):
                return self._bt.status

            def _rx(self, data: bytes):
                self.rxb += len(data)
                self.processIncoming(data)

            def processOutgoing(self, data):
                try:
                    self._bt.send(data)
                    self.txb += len(data)
                except Exception as e:
                    print(f"[RNODE_BT] TX error: {e}")

        return AndroidRNodeInterface()

    except Exception as e:
        print(f"[RNODE_BT] make_rns_interface failed: {e}")
        return None
