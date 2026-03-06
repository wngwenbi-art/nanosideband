"""
buildozer p4a hook — runs after p4a installs packages but before APK is built.
Patches RNS Android RNodeInterface with our RFCOMM implementation.
"""
import os, shutil

def after_pull(arch):
    _apply_patches()

def before_build(arch):
    _apply_patches()

def _apply_patches():
    here = os.path.dirname(os.path.abspath(__file__))
    patch = os.path.join(here, "patches", "RNS", "Interfaces", "Android", "RNodeInterface.py")
    if not os.path.exists(patch):
        print(f"[HOOK] Patch not found: {patch}")
        return
    # Walk the entire build tree looking for the file to replace
    for root, dirs, files in os.walk("/"):
        for fname in files:
            if fname == "RNodeInterface.py" and "Android" in root and "RNS" in root:
                dest = os.path.join(root, fname)
                shutil.copy2(patch, dest)
                print(f"[HOOK] Patched: {dest}")
