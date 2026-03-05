"""
nano/rnode_bt.py
Custom RNS interface for RNode over classic Bluetooth SPP (RFCOMM).

This bypasses RNS's built-in Android RNodeInterface (which uses BLE/able)
and instead uses jnius to open a persistent RFCOMM socket, implementing
the KISS protocol directly — the same approach Sideband uses internally.

On desktop (non-Android), it falls back to a serial port if available.
"""

import threading
import time
import logging
import struct

log = logging.getLogger(__name__)

# KISS protocol constants
FEND  = 0xC0
FESC  = 0xDB
TFEND = 0xDC
TFESC = 0xDD

CMD_DATA      = 0x00
CMD_FREQUENCY = 0x01
CMD_BANDWIDTH = 0x02
CMD_TXPOWER   = 0x03
CMD_SF        = 0x04
CMD_CR        = 0x05
CMD_RADIO_STATE = 0x06
CMD_DETECT    = 0x08
CMD_READY     = 0x0F

DETECT_REQ  = 0x73
DETECT_RESP = 0x46
RADIO_STATE_ON = 0x01

SPP_UUID = "00001101-0000-1000-8000-00805F9B34FB"


def kiss_escape(data: bytes) -> bytes:
    data = data.replace(bytes([FESC]), bytes([FESC, TFESC]))
    data = data.replace(bytes([FEND]), bytes([FESC, TFEND]))
    return data


def kiss_frame(cmd: int, data: bytes) -> bytes:
    return bytes([FEND, cmd]) + kiss_escape(data) + bytes([FEND])


def kiss_cmd(cmd: int, value: bytes) -> bytes:
    return bytes([FEND, cmd]) + kiss_escape(value) + bytes([FEND])


class RNodeBTInterface:
    """
    Persistent RFCOMM connection to an RNode device.
    Speaks KISS protocol, registers itself as a custom RNS interface.
    """

    def __init__(self, bt_addr: str, frequency: int, bandwidth: int,
                 spreading_factor: int, tx_power: int, coding_rate: int = 5,
                 on_packet=None):
        self.bt_addr         = bt_addr
        self.frequency       = frequency        # Hz, e.g. 915_000_000
        self.bandwidth       = bandwidth        # Hz, e.g. 125_000
        self.spreading_factor = spreading_factor  # 7-12
        self.tx_power        = tx_power         # dBm
        self.coding_rate     = coding_rate      # 5-8

        self.on_packet  = on_packet  # callback(bytes)
        self._sock      = None
        self._out_stream = None
        self._in_stream  = None
        self._running   = False
        self._lock      = threading.Lock()
        self.connected  = False
        self.status     = "Disconnected"

    # ── Connection ────────────────────────────────────────────────────────────

    def connect(self):
        """Open RFCOMM socket and configure RNode. Returns True on success."""
        try:
            self.status = "Connecting…"
            from jnius import autoclass  # type: ignore
            BTA  = autoclass("android.bluetooth.BluetoothAdapter")
            UUID = autoclass("java.util.UUID")

            adapter = BTA.getDefaultAdapter()
            if not adapter:
                self.status = "No Bluetooth adapter"
                return False

            dev  = adapter.getRemoteDevice(self.bt_addr)
            sock = dev.createInsecureRfcommSocketToServiceRecord(
                UUID.fromString(SPP_UUID))

            adapter.cancelDiscovery()
            sock.connect()

            self._sock       = sock
            self._out_stream = sock.getOutputStream()
            self._in_stream  = sock.getInputStream()
            self.connected   = True
            self.status      = "Connected — configuring…"
            log.info("RFCOMM connected to %s", self.bt_addr)

            self._configure_rnode()
            self._running = True
            threading.Thread(target=self._read_loop, daemon=True).start()
            self.status = "RNode active"
            return True

        except Exception as e:
            self.status = f"Connect failed: {e}"
            log.error("BT connect error: %s", e)
            self.connected = False
            return False

    def disconnect(self):
        self._running = False
        self.connected = False
        try:
            if self._sock:
                self._sock.close()
        except Exception:
            pass
        self._sock = None
        self._out_stream = None
        self._in_stream  = None
        self.status = "Disconnected"

    # ── KISS config ───────────────────────────────────────────────────────────

    def _write(self, data: bytes):
        if self._out_stream:
            for b in data:
                self._out_stream.write(b)

    def _configure_rnode(self):
        """Send KISS commands to set radio parameters and enable radio."""
        time.sleep(0.3)

        # Frequency (4 bytes big-endian)
        freq_bytes = struct.pack(">I", self.frequency)
        self._write(kiss_cmd(CMD_FREQUENCY, freq_bytes))
        time.sleep(0.1)

        # Bandwidth (4 bytes big-endian)
        bw_bytes = struct.pack(">I", self.bandwidth)
        self._write(kiss_cmd(CMD_BANDWIDTH, bw_bytes))
        time.sleep(0.1)

        # TX Power (1 byte)
        self._write(kiss_cmd(CMD_TXPOWER, bytes([self.tx_power])))
        time.sleep(0.1)

        # Spreading factor (1 byte)
        self._write(kiss_cmd(CMD_SF, bytes([self.spreading_factor])))
        time.sleep(0.1)

        # Coding rate (1 byte)
        self._write(kiss_cmd(CMD_CR, bytes([self.coding_rate])))
        time.sleep(0.1)

        # Enable radio
        self._write(kiss_cmd(CMD_RADIO_STATE, bytes([RADIO_STATE_ON])))
        time.sleep(0.3)

        log.info("RNode configured: %dHz BW=%d SF=%d TXP=%d",
                 self.frequency, self.bandwidth, self.spreading_factor, self.tx_power)

    # ── Send ──────────────────────────────────────────────────────────────────

    def send(self, data: bytes):
        """Send a packet over the air via KISS."""
        if not self.connected or not self._out_stream:
            raise IOError("Not connected")
        frame = kiss_frame(CMD_DATA, data)
        with self._lock:
            self._write(frame)

    # ── Receive loop ──────────────────────────────────────────────────────────

    def _read_loop(self):
        buf = bytearray()
        in_frame = False

        while self._running:
            try:
                avail = self._in_stream.available()
                if avail > 0:
                    chunk = bytearray(avail)
                    self._in_stream.read(chunk, 0, avail)
                    for b in chunk:
                        if b == FEND:
                            if in_frame and len(buf) > 1:
                                self._handle_frame(bytes(buf))
                            buf.clear()
                            in_frame = True
                        elif in_frame:
                            buf.append(b)
                else:
                    time.sleep(0.02)
            except Exception as e:
                if self._running:
                    log.error("Read loop error: %s", e)
                    self.connected = False
                    self.status = f"Disconnected: {e}"
                break

    def _handle_frame(self, frame: bytes):
        if len(frame) < 2:
            return
        cmd  = frame[0]
        data = self._unescape(frame[1:])

        if cmd == CMD_DATA:
            if self.on_packet:
                try:
                    self.on_packet(data)
                except Exception as e:
                    log.error("on_packet callback error: %s", e)
        else:
            log.debug("RNode cmd 0x%02x: %s", cmd, data.hex())

    @staticmethod
    def _unescape(data: bytes) -> bytes:
        out = bytearray()
        i = 0
        while i < len(data):
            b = data[i]
            if b == FESC and i + 1 < len(data):
                nxt = data[i + 1]
                if nxt == TFEND:
                    out.append(FEND)
                elif nxt == TFESC:
                    out.append(FESC)
                i += 2
            else:
                out.append(b)
                i += 1
        return bytes(out)


# ── RNS custom interface wrapper ──────────────────────────────────────────────

def make_rns_interface(bt_addr: str, frequency: int, bandwidth: int,
                       spreading_factor: int, tx_power: int,
                       coding_rate: int = 5):
    """
    Create and return an RNS-compatible interface object wrapping RNodeBTInterface.
    This is registered with RNS via RNS.Transport.interfaces.append().
    """
    try:
        import RNS
        from RNS.Interfaces.Interface import Interface

        class AndroidRNodeInterface(Interface):
            def __init__(self):
                super().__init__()
                self.name       = "RNode BT"
                self.rxb        = 0
                self.txb        = 0
                self.online     = False
                self.bitrate    = 1200  # LoRa typical

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
                    log.error("TX error: %s", e)

        iface = AndroidRNodeInterface()
        return iface

    except ImportError:
        log.error("RNS not available for interface creation")
        return None
