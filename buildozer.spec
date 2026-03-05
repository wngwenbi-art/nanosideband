[app]
title = NanoSideband
package.name = nanosideband
package.domain = org.nanosideband
version = 0.5.0

source.dir = .
source.include_exts = py,png,jpg,kv,atlas,txt,json
source.include_patterns = sbapp/*,nano/*


# Simplified requirements — no pinned python3, known working set
requirements = python3,kivy==2.3.0,pillow,cryptography,pyopenssl,cffi,pycparser,setuptools,rns,lxmf

android.api = 33
android.minapi = 26
# android.ndk = 25b  # use NDK from environment
android.accept_sdk_license = True

android.permissions =
    INTERNET,
    BLUETOOTH,
    BLUETOOTH_ADMIN,
    BLUETOOTH_CONNECT,
    BLUETOOTH_SCAN,
    ACCESS_FINE_LOCATION,
    ACCESS_COARSE_LOCATION,
    READ_EXTERNAL_STORAGE,
    WRITE_EXTERNAL_STORAGE,
    FOREGROUND_SERVICE

android.archs = arm64-v8a

orientation = portrait
fullscreen = 0

p4a.branch = master

[buildozer]
log_level = 2
warn_on_root = 0
