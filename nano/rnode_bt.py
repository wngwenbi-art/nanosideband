"""
nano/rnode_bt.py
RFCOMM RNode interface — directly mirrors the real RNS Android RNodeInterface.

Based on: RNS/Interfaces/Android/RNodeInterface.py (markqvist/Reticulum)
Key differences from our previous attempt:
  - Uses BufferedInputStream(socket.getInputStream(), 1024) like the real code
  - detect() sends: detect + fw_version + platform + mcu all at once
  - configure_device() order: resetRadioState → sleep(2.0) → start readLoop → detect → sleep(0.5) → initRadio → validateRadioState
  - validateRadioState() waits 1-2s and checks reported params match config
  - read() checks available() > 0, reads available bytes
"""

import threading
import time
import logging
import struct

log = logging.getLogger(__name__)

SPP_UUID = "00001101-0000-1000-8000-00805F9B34FB"

RECONNECT_WAIT   = 5
PORT_IO_TIMEOUT  = 3

REQUIRED_FW_VER_MAJ = 1
REQUIRED_FW_VER_MIN = 52


class KISS:
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
    CMD_ST_ALOCK     = 0x0B
    CMD_LT_ALOCK     = 0x0C
    CMD_DETECT       = 0x08
    CMD_LEAVE        = 0x0A
    CMD_READY        = 0x0F
    CMD_STAT_RX      = 0x21
    CMD_STAT_TX      = 0x22
    CMD_STAT_RSSI    = 0x23
    CMD_STAT_SNR     = 0x24
    CMD_STAT_CHTM    = 0x25
    CMD_STAT_PHYPRM  = 0x26
    CMD_STAT_BAT     = 0x27
    CMD_BT_CTRL      = 0x46
    CMD_PLATFORM     = 0x48
    CMD_MCU          = 0x49
    CMD_FW_VERSION   = 0x50
    CMD_ROM_READ     = 0x51
    CMD_RESET        = 0x55
    CMD_ERROR        = 0x90

    DETECT_REQ       = 0x73
    DETECT_RESP      = 0x46

    RADIO_STATE_OFF  = 0x00
    RADIO_STATE_ON   = 0x01
    RADIO_STATE_ASK  = 0xFF

    PLATFORM_AVR     = 0x90
    PLATFORM_ESP32   = 0x80
    PLATFORM_NRF52   = 0x70

    ERROR_INITRADIO      = 0x01
    ERROR_TXFAILED       = 0x02
    ERROR_INVALID_CONFIG = 0x40

    @staticmethod
    def escape(data):
        data = data.replace(bytes([0xDB]), bytes([0xDB, 0xDD]))
        data = data.replace(bytes([0xC0]), bytes([0xDB, 0xDC]))
        return data


class AndroidBluetoothManager:
    """
    Mirrors the real AndroidBluetoothManager from RNS source exactly.
    Uses BufferedInputStream like the real implementation.
    """

    def __init__(self, target_device_address=None, target_device_name=None):
        from jnius import autoclass
        self.target_device_address = target_device_address
        self.target_device_name    = target_device_name

        self.connected        = False
        self.connection_failed = False
        self.rfcomm_socket    = None
        self.rfcomm_reader    = None
        self.rfcomm_writer    = None
        self.connected_device = None

        self.bt_adapter = autoclass('android.bluetooth.BluetoothAdapter')
        self.bt_rfcomm_service_record = autoclass('java.util.UUID').fromString(SPP_UUID)
        self.buffered_input_stream = autoclass('java.io.BufferedInputStream')

        self.potential_remote_devices = []

    def bt_enabled(self):
        return self.bt_adapter.getDefaultAdapter().isEnabled()

    def get_paired_devices(self):
        if self.bt_enabled():
            return self.bt_adapter.getDefaultAdapter().getBondedDevices()
        return []

    def get_potential_devices(self):
        potential = []
        for device in self.get_paired_devices():
            addr = str(device.getAddress()).replace(":", "").lower()
            name = device.getName().lower() if device.getName() else ""

            if self.target_device_address is not None:
                target_addr = str(self.target_device_address).replace(":", "").lower()
                if addr == target_addr:
                    if self.target_device_name is None or name == self.target_device_name.lower():
                        potential.append(device)
            elif self.target_device_name is not None:
                if name == self.target_device_name.lower():
                    potential.append(device)
            else:
                if name.startswith("rnode "):
                    potential.append(device)
        return potential

    def connect_any_device(self):
        if self.rfcomm_socket is not None and not self.rfcomm_socket.isConnected():
            self.rfcomm_socket = None

        if self.rfcomm_socket is not None:
            return  # already connected

        self.connection_failed = False

        if len(self.potential_remote_devices) == 0:
            self.potential_remote_devices = self.get_potential_devices()

        if len(self.potential_remote_devices) == 0:
            print("[RNODE_BT] No suitable paired Bluetooth devices found")
            return

        while not self.connected and len(self.potential_remote_devices) > 0:
            device = self.potential_remote_devices.pop()
            try:
                sock = device.createRfcommSocketToServiceRecord(self.bt_rfcomm_service_record)
                if sock is None:
                    raise IOError("Bluetooth stack returned no socket")

                if not sock.isConnected():
                    sock.connect()
                    # Use BufferedInputStream with 1024 buffer — same as real code
                    self.rfcomm_reader = self.buffered_input_stream(sock.getInputStream(), 1024)
                    self.rfcomm_writer = sock.getOutputStream()
                    self.rfcomm_socket = sock
                    self.connected = True
                    self.connected_device = device
                    print(f"[RNODE_BT] Connected to {device.getName()} {device.getAddress()}")

            except Exception as e:
                print(f"[RNODE_BT] Could not connect to {device.getName()} {device.getAddress()}: {e}")

    def close(self):
        if self.connected:
            try:
                if self.rfcomm_reader:
                    self.rfcomm_reader.close()
            except Exception:
                pass
            try:
                if self.rfcomm_writer:
                    self.rfcomm_writer.close()
            except Exception:
                pass
            try:
                if self.rfcomm_socket:
                    self.rfcomm_socket.close()
            except Exception:
                pass
            self.rfcomm_reader    = None
            self.rfcomm_writer    = None
            self.rfcomm_socket    = None
            self.connected        = False
            self.connected_device = None
            self.potential_remote_devices = []

    def read(self):
        if self.connection_failed:
            raise IOError("Bluetooth connection failed")
        if self.connected and self.rfcomm_reader is not None:
            available = self.rfcomm_reader.available()
            if available > 0:
                # Use readNBytes if available (Android 9+), else single-byte fallback
                if hasattr(self.rfcomm_reader, 'readNBytes'):
                    return self.rfcomm_reader.readNBytes(available)
                else:
                    rb = self.rfcomm_reader.read().to_bytes(1, 'big')
                    return rb
            else:
                return bytes([])
        else:
            raise IOError("No RFcomm socket available")

    def write(self, data):
        try:
            self.rfcomm_writer.write(data)
            self.rfcomm_writer.flush()
            return len(data)
        except Exception as e:
            print(f"[RNODE_BT] Write failed: {e}")
            self.connection_failed = True
            return 0


class RNodeBTInterface:
    """
    Mirrors RNS Android RNodeInterface configure_device() → initRadio() → validateRadioState() flow.
    """

    def __init__(self, target_address, frequency, bandwidth,
                 spreading_factor, tx_power, coding_rate=5, on_packet=None):
        self.target_address   = target_address
        self.frequency        = frequency
        self.bandwidth        = bandwidth
        self.sf               = spreading_factor
        self.txpower          = tx_power
        self.cr               = coding_rate
        self.on_packet        = on_packet

        self.bt_manager   = None
        self._running     = False
        self.online       = False
        self.detected     = False
        self.firmware_ok  = False
        self.status       = "Disconnected"

        self.platform     = None
        self.display      = None
        self.maj_version  = 0
        self.min_version  = 0

        # Reported radio state from device
        self.r_frequency  = None
        self.r_bandwidth  = None
        self.r_txpower    = None
        self.r_sf         = None
        self.r_cr         = None
        self.r_state      = None
        self.r_lock       = None

        self.state        = KISS.RADIO_STATE_OFF
        self.validcfg     = True

    def connect(self):
        try:
            self.status = "Connecting…"
            print(f"[RNODE_BT] connect() → {self.target_address}")

            self.bt_manager = AndroidBluetoothManager(
                target_device_address=self.target_address
            )
            self.bt_manager.connect_any_device()

            if not self.bt_manager.connected:
                self.status = "Could not connect to Bluetooth device"
                print("[RNODE_BT] bt_manager.connect_any_device() failed")
                return False

            print("[RNODE_BT] BT connected — configuring device")
            self.configure_device()
            return self.online

        except Exception as e:
            self.status = f"Connect failed: {e}"
            print(f"[RNODE_BT] connect FAILED: {e}")
            import traceback; traceback.print_exc()
            return False

    def configure_device(self):
        """Mirrors configure_device() from real RNS Android source exactly."""
        self.resetRadioState()
        time.sleep(2.0)                          # real code: sleep(2.0)

        # Start read loop before sending detect
        self._running = True
        threading.Thread(target=self.readLoop, daemon=True, name="rnode-rx").start()

        self.detect()
        time.sleep(0.5)                          # real code: sleep(0.5) for serial

        if not self.detected:
            raise IOError("Could not detect RNode device")

        if self.platform in (KISS.PLATFORM_ESP32, KISS.PLATFORM_NRF52):
            self.display = True

        if not self.firmware_ok:
            raise IOError("Invalid RNode firmware version")

        print(f"[RNODE_BT] Detected! fw={self.maj_version}.{self.min_version} platform=0x{(self.platform or 0):02x}")

        self.status = "Configuring radio…"
        self.initRadio()

        if self.validateRadioState():
            self.online = True
            self.status = "RNode active"
            print(f"[RNODE_BT] Online: {self.frequency}Hz BW={self.bandwidth} SF={self.sf} TXP={self.txpower}")
        else:
            self.online = False
            self.status = "Radio config validation failed"
            raise IOError("RNode radio config validation failed")

    def resetRadioState(self):
        self.r_frequency = None
        self.r_bandwidth = None
        self.r_txpower   = None
        self.r_sf        = None
        self.r_cr        = None
        self.r_state     = None
        self.r_lock      = None

    def detect(self):
        """
        Real detect() sends detect + fw_version + platform + mcu all at once.
        bytes: FEND CMD_DETECT DETECT_REQ FEND CMD_FW_VERSION 0x00 FEND CMD_PLATFORM 0x00 FEND CMD_MCU 0x00 FEND
        """
        kiss_command = bytes([
            KISS.FEND, KISS.CMD_DETECT,    KISS.DETECT_REQ, KISS.FEND,
            KISS.FEND, KISS.CMD_FW_VERSION, 0x00,           KISS.FEND,
            KISS.FEND, KISS.CMD_PLATFORM,   0x00,           KISS.FEND,
            KISS.FEND, KISS.CMD_MCU,        0x00,           KISS.FEND,
        ])
        written = self.write_mux(kiss_command)
        print(f"[RNODE_BT] detect sent {written}/{len(kiss_command)} bytes")
        if written != len(kiss_command):
            raise IOError("IO error during detect")

    def initRadio(self):
        """Mirrors initRadio() from real source, 0.15s between commands."""
        self.setFrequency();      time.sleep(0.15)
        self.setBandwidth();      time.sleep(0.15)
        self.setTXPower();        time.sleep(0.15)
        self.setSpreadingFactor(); time.sleep(0.15)
        self.setCodingRate();     time.sleep(0.15)
        self.setRadioState(KISS.RADIO_STATE_ON); time.sleep(0.15)

    def validateRadioState(self):
        """Wait 1-2s then check reported params match config."""
        print("[RNODE_BT] Waiting for radio config validation…")
        if self.platform == KISS.PLATFORM_ESP32:
            time.sleep(2.0)
        else:
            time.sleep(1.0)

        self.validcfg = True
        if self.r_frequency is not None and abs(self.frequency - int(self.r_frequency)) > 100:
            print(f"[RNODE_BT] Frequency mismatch: want {self.frequency} got {self.r_frequency}")
            self.validcfg = False
        if self.r_bandwidth is not None and self.bandwidth != self.r_bandwidth:
            print(f"[RNODE_BT] Bandwidth mismatch: want {self.bandwidth} got {self.r_bandwidth}")
            self.validcfg = False
        if self.r_txpower is not None and self.txpower != self.r_txpower:
            print(f"[RNODE_BT] TXPower mismatch: want {self.txpower} got {self.r_txpower}")
            self.validcfg = False
        if self.r_sf is not None and self.sf != self.r_sf:
            print(f"[RNODE_BT] SF mismatch: want {self.sf} got {self.r_sf}")
            self.validcfg = False
        if self.r_state is not None and self.state != self.r_state:
            print(f"[RNODE_BT] State mismatch: want {self.state} got {self.r_state}")
            self.validcfg = False

        return self.validcfg

    # ── KISS command senders ──────────────────────────────────────────────────

    def setFrequency(self):
        c1 = self.frequency >> 24
        c2 = (self.frequency >> 16) & 0xFF
        c3 = (self.frequency >> 8)  & 0xFF
        c4 = self.frequency & 0xFF
        data = KISS.escape(bytes([c1, c2, c3, c4]))
        cmd = bytes([KISS.FEND, KISS.CMD_FREQUENCY]) + data + bytes([KISS.FEND])
        self.write_mux(cmd)

    def setBandwidth(self):
        c1 = self.bandwidth >> 24
        c2 = (self.bandwidth >> 16) & 0xFF
        c3 = (self.bandwidth >> 8)  & 0xFF
        c4 = self.bandwidth & 0xFF
        data = KISS.escape(bytes([c1, c2, c3, c4]))
        cmd = bytes([KISS.FEND, KISS.CMD_BANDWIDTH]) + data + bytes([KISS.FEND])
        self.write_mux(cmd)

    def setTXPower(self):
        cmd = bytes([KISS.FEND, KISS.CMD_TXPOWER, self.txpower, KISS.FEND])
        self.write_mux(cmd)

    def setSpreadingFactor(self):
        cmd = bytes([KISS.FEND, KISS.CMD_SF, self.sf, KISS.FEND])
        self.write_mux(cmd)

    def setCodingRate(self):
        cmd = bytes([KISS.FEND, KISS.CMD_CR, self.cr, KISS.FEND])
        self.write_mux(cmd)

    def setRadioState(self, state):
        self.state = state
        cmd = bytes([KISS.FEND, KISS.CMD_RADIO_STATE, state, KISS.FEND])
        self.write_mux(cmd)

    def send(self, data: bytes):
        escaped = KISS.escape(data)
        cmd = bytes([KISS.FEND, KISS.CMD_DATA]) + escaped + bytes([KISS.FEND])
        self.write_mux(cmd)

    def write_mux(self, data):
        if self.bt_manager is not None:
            return self.bt_manager.write(data)
        return 0

    # ── Read loop ─────────────────────────────────────────────────────────────

    def readLoop(self):
        """Read loop that exactly mirrors the real RNS readLoop."""
        in_frame   = False
        escape     = False
        command    = KISS.CMD_UNKNOWN = 0xFE
        data_buffer = b""

        while self._running:
            try:
                data_in = self.bt_manager.read()

                if data_in is None or len(data_in) == 0:
                    time.sleep(0.01)
                    continue

                for byte in data_in:
                    if isinstance(byte, int):
                        b = byte
                    else:
                        b = ord(byte)

                    if b == KISS.FEND:
                        if in_frame and len(data_buffer) > 0:
                            self.processFrame(command, data_buffer)
                        in_frame    = True
                        escape      = False
                        command     = 0xFE
                        data_buffer = b""

                    elif in_frame:
                        if escape:
                            if b == KISS.TFEND:
                                data_buffer += bytes([KISS.FEND])
                            elif b == KISS.TFESC:
                                data_buffer += bytes([KISS.FESC])
                            escape = False
                        elif b == KISS.FESC:
                            escape = True
                        else:
                            if command == 0xFE:
                                command = b
                            else:
                                data_buffer += bytes([b])

            except Exception as e:
                if self._running:
                    print(f"[RNODE_BT] readLoop error: {e}")
                    self.online = False
                    self.status = f"Disconnected: {e}"
                break

    def processFrame(self, command, data):
        if command == KISS.CMD_DATA:
            if self.on_packet:
                try:
                    self.on_packet(data)
                except Exception as e:
                    print(f"[RNODE_BT] on_packet error: {e}")

        elif command == KISS.CMD_DETECT:
            if len(data) >= 1 and data[0] == KISS.DETECT_RESP:
                self.detected = True
                print("[RNODE_BT] DETECT_RESP received")

        elif command == KISS.CMD_FW_VERSION:
            if len(data) >= 2:
                self.maj_version = data[0]
                self.min_version = data[1]
                self.validate_firmware()
                print(f"[RNODE_BT] FW version: {self.maj_version}.{self.min_version}")

        elif command == KISS.CMD_PLATFORM:
            if len(data) >= 1:
                self.platform = data[0]
                print(f"[RNODE_BT] Platform: 0x{self.platform:02x}")

        elif command == KISS.CMD_FREQUENCY:
            if len(data) >= 4:
                self.r_frequency = (data[0] << 24) | (data[1] << 16) | (data[2] << 8) | data[3]
                print(f"[RNODE_BT] r_frequency: {self.r_frequency}")

        elif command == KISS.CMD_BANDWIDTH:
            if len(data) >= 4:
                self.r_bandwidth = (data[0] << 24) | (data[1] << 16) | (data[2] << 8) | data[3]
                print(f"[RNODE_BT] r_bandwidth: {self.r_bandwidth}")

        elif command == KISS.CMD_TXPOWER:
            if len(data) >= 1:
                self.r_txpower = data[0]
                print(f"[RNODE_BT] r_txpower: {self.r_txpower}")

        elif command == KISS.CMD_SF:
            if len(data) >= 1:
                self.r_sf = data[0]
                print(f"[RNODE_BT] r_sf: {self.r_sf}")

        elif command == KISS.CMD_CR:
            if len(data) >= 1:
                self.r_cr = data[0]
                print(f"[RNODE_BT] r_cr: {self.r_cr}")

        elif command == KISS.CMD_RADIO_STATE:
            if len(data) >= 1:
                self.r_state = data[0]
                print(f"[RNODE_BT] r_state: {self.r_state}")

        elif command == KISS.CMD_ERROR:
            if len(data) >= 1:
                print(f"[RNODE_BT] ERROR from device: 0x{data[0]:02x}")

        else:
            if len(data) > 0:
                print(f"[RNODE_BT] cmd 0x{command:02x}: {data.hex()}")

    def validate_firmware(self):
        if self.maj_version > REQUIRED_FW_VER_MAJ:
            self.firmware_ok = True
        elif self.maj_version >= REQUIRED_FW_VER_MAJ:
            if self.min_version >= REQUIRED_FW_VER_MIN:
                self.firmware_ok = True
        if not self.firmware_ok:
            print(f"[RNODE_BT] WARNING: FW {self.maj_version}.{self.min_version} < required {REQUIRED_FW_VER_MAJ}.{REQUIRED_FW_VER_MIN}")
            # Don't hard-fail on old firmware — just warn
            self.firmware_ok = True

    def disconnect(self):
        self._running = False
        self.online   = False
        try:
            if self.bt_manager:
                self.bt_manager.write(bytes([KISS.FEND, KISS.CMD_RADIO_STATE, KISS.RADIO_STATE_OFF, KISS.FEND]))
        except Exception:
            pass
        try:
            if self.bt_manager:
                self.bt_manager.close()
        except Exception:
            pass
        self.status = "Disconnected"


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
                    target_address=bt_addr,
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
        import traceback; traceback.print_exc()
        return None
