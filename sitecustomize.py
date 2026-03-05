"""
sitecustomize.py - baked into APK, runs before any user code.
Stubs missing C extensions so RNS can import on this p4a build.
"""
import sys
import types

# Stub _bz2 - not compiled in this p4a build, needed by RNS/Buffer.py
if '_bz2' not in sys.modules:
    _bz2 = types.ModuleType('_bz2')
    class BZ2Compressor:
        def __init__(self, level=9): pass
        def compress(self, d): return d
        def flush(self): return b''
    class BZ2Decompressor:
        def decompress(self, d, *a): return d
        eof = False
        unused_data = b''
        needs_input = True
    _bz2.BZ2Compressor = BZ2Compressor
    _bz2.BZ2Decompressor = BZ2Decompressor
    sys.modules['_bz2'] = _bz2

# Stub able - BLE Java class missing, we use RFCOMM instead
if 'able' not in sys.modules:
    able = types.ModuleType('able')
    able.BluetoothDispatcher = object
    able.GATT_SUCCESS = 0
    able.GATT_ERROR = 133
    sys.modules['able'] = able

print("[SITECUSTOMIZE] stubs OK")
