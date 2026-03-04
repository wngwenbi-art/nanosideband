"""
main.py — entry point for buildozer / Android.
Delegates to sbapp/main.py.
"""
import os
import sys

# Ensure sbapp and nano are importable
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from sbapp.main import NanoSidebandApp

if __name__ == "__main__":
    NanoSidebandApp().run()
