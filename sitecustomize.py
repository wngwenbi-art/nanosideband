"""
sitecustomize.py - runs before any import on Android.
Patches RNS Android RNodeInterface to not crash when able_recipe is missing.
"""
import sys

if hasattr(sys, 'getandroidapilevel'):
    import types

    # Create a fake 'able' module so RNS's `from able import ...` doesn't crash
    able_mod = types.ModuleType('able')
    able_mod.BluetoothDispatcher = object
    able_mod.GATT_SUCCESS = 0
    able_mod.GATT_ERROR = 133
    sys.modules['able'] = able_mod
