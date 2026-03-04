[app]
# App identity
title = NanoSideband
package.name = nanosideband
package.domain = org.nanosideband
version = 0.5.0

# Source
source.dir = .
source.include_exts = py,png,jpg,kv,atlas,txt,json
source.include_patterns = sbapp/*,nano/*,nano/**/*

# Entry point
entrypoint = sbapp/main.py

# Requirements — pinned to versions known to work with python-for-android
requirements =
    python3==3.11.6,
    kivy==2.3.0,
    pillow==10.2.0,
    cryptography==42.0.5,
    pyopenssl==24.0.0,
    cffi,
    pycparser,
    setuptools

# Android target
android.api = 33
android.minapi = 26
android.ndk = 25b
android.sdk = 33
android.accept_sdk_license = True

# Permissions
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
    FOREGROUND_SERVICE,
    REQUEST_INSTALL_PACKAGES

# Architecture — arm64 for modern phones; add armeabi-v7a for older
android.archs = arm64-v8a

# App icon & presplash
#android.icon = assets/icon.png
#android.presplash = assets/presplash.png

# Orientation
orientation = portrait

# Fullscreen
fullscreen = 0

# Android features
android.features = android.hardware.bluetooth

# Gradle dependencies for Bluetooth
android.gradle_dependencies = 

# Keep app running in background
android.services = 

# Build mode
android.release_artifact = apk

# p4a branch
p4a.branch = master

# Log level
log_level = 2

[buildozer]
log_level = 2
warn_on_root = 0
