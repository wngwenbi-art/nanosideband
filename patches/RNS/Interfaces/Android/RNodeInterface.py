# NanoSideband patched RNodeInterface for Android
# Replaces the BLE (able) implementation with RFCOMM via AndroidBluetoothManager.
# This file is copied over the RNS package's Android/RNodeInterface.py at build time.

from RNS.Interfaces.Interface import Interface
from time import sleep
import threading
import time
import RNS

# able is not available in our build — stub it out silently
try:
    from able import BluetoothDispatcher, GATT_SUCCESS
except Exception:
    GATT_SUCCESS = 0x00
    class BluetoothDispatcher:
        def __init__(self, **kwargs):
            raise OSError("BLE not available")


class KISS:
    FEND  = 0xC0
    FESC  = 0xDB
    TFEND = 0xDC
    TFESC = 0xDD

    CMD_UNKNOWN      = 0xFE
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
    CMD_BT_CTRL      = 0x46
    CMD_PLATFORM     = 0x48
    CMD_MCU          = 0x49
    CMD_FW_VERSION   = 0x50
    CMD_ERROR        = 0x90

    DETECT_REQ       = 0x73
    DETECT_RESP      = 0x46
    RADIO_STATE_OFF  = 0x00
    RADIO_STATE_ON   = 0x01
    RADIO_STATE_ASK  = 0xFF

    PLATFORM_ESP32   = 0x80
    PLATFORM_NRF52   = 0x70

    @staticmethod
    def escape(data):
        data = data.replace(bytes([0xDB]), bytes([0xDB, 0xDD]))
        data = data.replace(bytes([0xC0]), bytes([0xDB, 0xDC]))
        return data


class AndroidBluetoothManager:
    def __init__(self, owner, target_device_name=None, target_device_address=None):
        from jnius import autoclass
        self.owner = owner
        self.target_device_name    = target_device_name
        self.target_device_address = target_device_address
        self.connected             = False
        self.connection_failed     = False
        self.rfcomm_socket         = None
        self.rfcomm_reader         = None
        self.rfcomm_writer         = None
        self.connected_device      = None
        self.potential_remote_devices = []

        self.bt_adapter = autoclass('android.bluetooth.BluetoothAdapter')
        self.bt_rfcomm_service_record = autoclass('java.util.UUID').fromString(
            "00001101-0000-1000-8000-00805F9B34FB")
        self.buffered_input_stream = autoclass('java.io.BufferedInputStream')

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
            name = (device.getName() or "").lower()
            if self.target_device_address is not None:
                target = str(self.target_device_address).replace(":", "").lower()
                if addr == target:
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
        if self.rfcomm_socket is not None and self.rfcomm_socket.isConnected():
            return
        self.connection_failed = False
        if not self.potential_remote_devices:
            self.potential_remote_devices = self.get_potential_devices()
        if not self.potential_remote_devices:
            RNS.log("No suitable Bluetooth devices found for RNode", RNS.LOG_ERROR)
            return
        while not self.connected and self.potential_remote_devices:
            device = self.potential_remote_devices.pop()
            try:
                sock = device.createRfcommSocketToServiceRecord(self.bt_rfcomm_service_record)
                if sock is None:
                    raise IOError("Bluetooth stack returned no socket")
                sock.connect()
                self.rfcomm_reader  = self.buffered_input_stream(sock.getInputStream(), 1024)
                self.rfcomm_writer  = sock.getOutputStream()
                self.rfcomm_socket  = sock
                self.connected      = True
                self.connected_device = device
                RNS.log(f"Bluetooth connected to {device.getName()} {device.getAddress()}")
            except Exception as e:
                RNS.log(f"Could not connect BT to {device.getName()}: {e}", RNS.LOG_ERROR)

    def close(self):
        for attr in ('rfcomm_reader', 'rfcomm_writer', 'rfcomm_socket'):
            try:
                obj = getattr(self, attr, None)
                if obj:
                    obj.close()
            except Exception:
                pass
        self.rfcomm_reader = None
        self.rfcomm_writer = None
        self.rfcomm_socket = None
        self.connected = False
        self.connected_device = None
        self.potential_remote_devices = []

    def read(self):
        if self.connection_failed:
            raise IOError("Bluetooth connection failed")
        if self.connected and self.rfcomm_reader is not None:
            available = self.rfcomm_reader.available()
            if available > 0:
                if hasattr(self.rfcomm_reader, 'readNBytes'):
                    return self.rfcomm_reader.readNBytes(available)
                else:
                    return self.rfcomm_reader.read().to_bytes(1, 'big')
            return bytes([])
        raise IOError("No RFcomm socket available")

    def write(self, data):
        try:
            self.rfcomm_writer.write(data)
            self.rfcomm_writer.flush()
            return len(data)
        except Exception as e:
            RNS.log(f"Bluetooth write failed: {e}", RNS.LOG_ERROR)
            self.connection_failed = True
            return 0


class RNodeInterface(Interface):
    HW_MTU              = 508
    BITRATE_MINIMUM     = 1
    RECONNECT_WAIT      = 5
    REQUIRED_FW_VER_MAJ = 1
    REQUIRED_FW_VER_MIN = 52

    def __init__(self, owner, configuration):
        c = Interface.get_config_obj(configuration)
        name = c["name"]

        allow_bluetooth       = c.as_bool("allow_bluetooth") if "allow_bluetooth" in c else False
        target_device_name    = c["target_device_name"]    if "target_device_name"    in c else None
        target_device_address = c["target_device_address"] if "target_device_address" in c else None
        frequency  = int(c["frequency"])      if "frequency"      in c else 0
        bandwidth  = int(c["bandwidth"])      if "bandwidth"      in c else 0
        txpower    = int(c["txpower"])        if "txpower"        in c else 0
        sf         = int(c["spreadingfactor"])if "spreadingfactor"in c else 0
        cr         = int(c["codingrate"])     if "codingrate"     in c else 5

        super().__init__()
        self.name       = name
        self.owner      = owner
        self.frequency  = frequency
        self.bandwidth  = bandwidth
        self.txpower    = txpower
        self.sf         = sf
        self.cr         = cr
        self.online     = False
        self.detached   = False
        self.detected   = False
        self.firmware_ok= False
        self.platform   = None
        self.display    = None
        self.state      = KISS.RADIO_STATE_OFF
        self.bitrate    = 0

        self.r_frequency = self.r_bandwidth = self.r_txpower = None
        self.r_sf = self.r_cr = self.r_state = None

        self.maj_version = 0
        self.min_version = 0
        self.validcfg    = True
        self._running    = False

        self.bt_manager = None
        if allow_bluetooth:
            self.bt_manager = AndroidBluetoothManager(
                owner=self,
                target_device_name=target_device_name,
                target_device_address=target_device_address,
            )
        else:
            raise ValueError(f"RNodeInterface {name}: allow_bluetooth must be True for Android BT")

        try:
            self.bt_manager.connect_any_device()
            if self.bt_manager.connected:
                self.configure_device()
            else:
                raise IOError("Could not connect to any Bluetooth RNode device")
        except Exception as e:
            RNS.log(f"RNodeInterface {name} failed: {e}", RNS.LOG_ERROR)
            RNS.log("Will retry...", RNS.LOG_ERROR)
            threading.Thread(target=self.reconnect_port, daemon=True).start()

    def read_mux(self):
        return self.bt_manager.read()

    def write_mux(self, data):
        if self.bt_manager is not None:
            written = self.bt_manager.write(data)
            return written
        raise IOError("No transport available")

    def configure_device(self):
        self.r_frequency = self.r_bandwidth = self.r_txpower = None
        self.r_sf = self.r_cr = self.r_state = None
        sleep(2.0)
        self._running = True
        threading.Thread(target=self.readLoop, daemon=True, name=f"rnode-rx-{self.name}").start()
        self.detect()
        sleep(0.5)
        if not self.detected:
            raise IOError("RNode did not respond to detect")
        if self.platform in (KISS.PLATFORM_ESP32, KISS.PLATFORM_NRF52):
            self.display = True
        RNS.log(f"RNode detected fw={self.maj_version}.{self.min_version}", RNS.LOG_VERBOSE)
        self.initRadio()
        if self.validateRadioState():
            self.online = True
            RNS.log(f"{self} is online", RNS.LOG_VERBOSE)
        else:
            raise IOError("RNode radio config validation failed")

    def detect(self):
        cmd = bytes([
            KISS.FEND, KISS.CMD_DETECT,     KISS.DETECT_REQ, KISS.FEND,
            KISS.FEND, KISS.CMD_FW_VERSION,  0x00,           KISS.FEND,
            KISS.FEND, KISS.CMD_PLATFORM,    0x00,           KISS.FEND,
            KISS.FEND, KISS.CMD_MCU,         0x00,           KISS.FEND,
        ])
        self.write_mux(cmd)

    def initRadio(self):
        self.setFrequency();       time.sleep(0.15)
        self.setBandwidth();       time.sleep(0.15)
        self.setTXPower();         time.sleep(0.15)
        self.setSpreadingFactor(); time.sleep(0.15)
        self.setCodingRate();      time.sleep(0.15)
        self.setRadioState(KISS.RADIO_STATE_ON); time.sleep(0.15)

    def validateRadioState(self):
        RNS.log(f"Validating radio state for {self}...", RNS.LOG_VERBOSE)
        sleep(2.0 if self.platform == KISS.PLATFORM_ESP32 else 1.0)
        self.validcfg = True
        if self.r_frequency is not None and abs(self.frequency - int(self.r_frequency)) > 100:
            RNS.log("Frequency mismatch", RNS.LOG_ERROR); self.validcfg = False
        if self.r_bandwidth is not None and self.bandwidth != self.r_bandwidth:
            RNS.log("Bandwidth mismatch", RNS.LOG_ERROR); self.validcfg = False
        if self.r_txpower is not None and self.txpower != self.r_txpower:
            RNS.log("TXPower mismatch", RNS.LOG_ERROR); self.validcfg = False
        if self.r_sf is not None and self.sf != self.r_sf:
            RNS.log("SF mismatch", RNS.LOG_ERROR); self.validcfg = False
        if self.r_state is not None and self.state != self.r_state:
            RNS.log("Radio state mismatch", RNS.LOG_ERROR); self.validcfg = False
        return self.validcfg

    def setFrequency(self):
        c1,c2,c3,c4 = self.frequency>>24,(self.frequency>>16)&0xFF,(self.frequency>>8)&0xFF,self.frequency&0xFF
        self.write_mux(bytes([KISS.FEND,KISS.CMD_FREQUENCY])+KISS.escape(bytes([c1,c2,c3,c4]))+bytes([KISS.FEND]))

    def setBandwidth(self):
        c1,c2,c3,c4 = self.bandwidth>>24,(self.bandwidth>>16)&0xFF,(self.bandwidth>>8)&0xFF,self.bandwidth&0xFF
        self.write_mux(bytes([KISS.FEND,KISS.CMD_BANDWIDTH])+KISS.escape(bytes([c1,c2,c3,c4]))+bytes([KISS.FEND]))

    def setTXPower(self):
        self.write_mux(bytes([KISS.FEND,KISS.CMD_TXPOWER,self.txpower,KISS.FEND]))

    def setSpreadingFactor(self):
        self.write_mux(bytes([KISS.FEND,KISS.CMD_SF,self.sf,KISS.FEND]))

    def setCodingRate(self):
        self.write_mux(bytes([KISS.FEND,KISS.CMD_CR,self.cr,KISS.FEND]))

    def setRadioState(self, state):
        self.state = state
        self.write_mux(bytes([KISS.FEND,KISS.CMD_RADIO_STATE,state,KISS.FEND]))

    def processOutgoing(self, data):
        escaped = KISS.escape(data)
        self.write_mux(bytes([KISS.FEND,KISS.CMD_DATA])+escaped+bytes([KISS.FEND]))

    def readLoop(self):
        in_frame    = False
        escape      = False
        command     = KISS.CMD_UNKNOWN
        data_buffer = b""

        while self._running:
            try:
                data_in = self.read_mux()
                if not data_in:
                    time.sleep(0.01)
                    continue
                for b in data_in:
                    if isinstance(b, int):
                        byte = b
                    else:
                        byte = ord(b)

                    if byte == KISS.FEND:
                        if in_frame and data_buffer:
                            self._dispatch(command, data_buffer)
                        in_frame    = True
                        escape      = False
                        command     = KISS.CMD_UNKNOWN
                        data_buffer = b""
                    elif in_frame:
                        if escape:
                            if byte == KISS.TFEND: data_buffer += bytes([KISS.FEND])
                            elif byte == KISS.TFESC: data_buffer += bytes([KISS.FESC])
                            escape = False
                        elif byte == KISS.FESC:
                            escape = True
                        else:
                            if command == KISS.CMD_UNKNOWN:
                                command = byte
                            else:
                                data_buffer += bytes([byte])
            except Exception as e:
                if self._running:
                    RNS.log(f"{self} read error: {e}", RNS.LOG_ERROR)
                    self.online = False
                    self._running = False
                break

    def _dispatch(self, command, data):
        if command == KISS.CMD_DATA:
            self.processIncoming(data)
        elif command == KISS.CMD_DETECT:
            if data and data[0] == KISS.DETECT_RESP:
                self.detected = True
        elif command == KISS.CMD_FW_VERSION:
            if len(data) >= 2:
                self.maj_version = data[0]
                self.min_version = data[1]
                self._validate_firmware()
        elif command == KISS.CMD_PLATFORM:
            if data: self.platform = data[0]
        elif command == KISS.CMD_FREQUENCY:
            if len(data) >= 4:
                self.r_frequency = (data[0]<<24)|(data[1]<<16)|(data[2]<<8)|data[3]
        elif command == KISS.CMD_BANDWIDTH:
            if len(data) >= 4:
                self.r_bandwidth = (data[0]<<24)|(data[1]<<16)|(data[2]<<8)|data[3]
        elif command == KISS.CMD_TXPOWER:
            if data: self.r_txpower = data[0]
        elif command == KISS.CMD_SF:
            if data: self.r_sf = data[0]
        elif command == KISS.CMD_CR:
            if data: self.r_cr = data[0]
        elif command == KISS.CMD_RADIO_STATE:
            if data: self.r_state = data[0]
        elif command == KISS.CMD_ERROR:
            if data: RNS.log(f"{self} hardware error: 0x{data[0]:02x}", RNS.LOG_ERROR)

    def _validate_firmware(self):
        if (self.maj_version > self.REQUIRED_FW_VER_MAJ or
            (self.maj_version >= self.REQUIRED_FW_VER_MAJ and
             self.min_version >= self.REQUIRED_FW_VER_MIN)):
            self.firmware_ok = True
        else:
            RNS.log(f"RNode FW {self.maj_version}.{self.min_version} may be too old", RNS.LOG_WARNING)
            self.firmware_ok = True  # warn but don't block

    def reconnect_port(self):
        while not self.online:
            time.sleep(self.RECONNECT_WAIT)
            RNS.log(f"Attempting to reconnect {self}...", RNS.LOG_VERBOSE)
            try:
                if self.bt_manager:
                    self.bt_manager.close()
                self.bt_manager.potential_remote_devices = []
                self.bt_manager.connect_any_device()
                if self.bt_manager.connected:
                    self.configure_device()
            except Exception as e:
                RNS.log(f"Reconnect failed: {e}", RNS.LOG_ERROR)

    def __str__(self):
        return f"RNodeInterface[{self.name}]"
