#!/usr/bin/env python3
"""
scripts/smoke_test_image.py
Phase 3 smoke test — image compression layer.

Run from the project root:
    python scripts/smoke_test_image.py
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

PASS = "\033[92m✓\033[0m"
FAIL = "\033[91m✗\033[0m"
INFO = "\033[94m·\033[0m"


def check(label: str, condition: bool, detail: str = "") -> bool:
    tag = PASS if condition else FAIL
    suffix = f"  ({detail})" if detail else ""
    print(f"  {tag} {label}{suffix}")
    return condition


def make_test_image(width=1200, height=900, color=(100, 150, 200)) -> bytes:
    """Create a synthetic test image using Pillow."""
    from PIL import Image, ImageDraw
    import io
    img = Image.new("RGB", (width, height), color)
    draw = ImageDraw.Draw(img)
    # Add some variation so compression isn't trivially small
    for i in range(0, width, 40):
        draw.line([(i, 0), (i, height)], fill=(200, 100, 50), width=2)
    for j in range(0, height, 40):
        draw.line([(0, j), (width, j)], fill=(50, 200, 100), width=2)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def run():
    print("\n--- NanoSideband Phase 3 Smoke Test: Images ---\n")
    all_ok = True

    # ── 0. Check Pillow ───────────────────────────────────────────────────────
    print("[0] Checking Pillow...")
    try:
        from PIL import Image
        import PIL
        all_ok &= check("Pillow available", True, PIL.__version__)
    except ImportError:
        all_ok &= check("Pillow available", False,
                        "run: pip install pillow")
        print(f"\n  Pillow is required for Phase 3. Skipping remaining tests.\n")
        return 1

    from nano.image import (
        compress, decompress, fits_in_packet,
        image_to_field, field_to_display, image_dimensions,
        _PIL_AVAILABLE, _detect_mime, JPEG_MAGIC,
    )
    all_ok &= check("nano.image imports", True)
    all_ok &= check("_PIL_AVAILABLE is True", _PIL_AVAILABLE)

    # ── 1. MIME detection ─────────────────────────────────────────────────────
    print("\n[1] MIME detection...")
    all_ok &= check("JPEG detected",  _detect_mime(b"\xff\xd8\xff" + b"\x00"*10) == "image/jpeg")
    all_ok &= check("PNG detected",   _detect_mime(b"\x89PNG" + b"\x00"*10) == "image/png")
    all_ok &= check("Unknown type",   _detect_mime(b"\x00\x01\x02\x03") == "application/octet-stream")

    # ── 2. Compress ───────────────────────────────────────────────────────────
    print("\n[2] Compress...")
    original = make_test_image(1200, 900)
    print(f"  {INFO} Original size: {len(original):,} bytes ({len(original)//1024} KB)")

    compressed = compress(original, max_width=800, quality=70)
    print(f"  {INFO} Compressed:    {len(compressed):,} bytes ({len(compressed)//1024} KB)")

    all_ok &= check("Compress returns bytes",      isinstance(compressed, bytes))
    all_ok &= check("Compressed is JPEG",          compressed[:3] == JPEG_MAGIC)
    all_ok &= check("Compressed smaller",          len(compressed) < len(original))

    # Check dimensions were resized
    w, h = image_dimensions(compressed)
    all_ok &= check("Width capped at 800",         w == 800, f"got {w}")
    all_ok &= check("Aspect ratio preserved",      h == 600, f"got {h}  (expected 600 for 1200x900 -> 800x600)")

    # Compress from file path
    import tempfile, os
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        f.write(original)
        tmp_path = f.name
    try:
        from_file = compress(tmp_path, max_width=400, quality=60)
        all_ok &= check("Compress from file path", isinstance(from_file, bytes))
        w2, h2 = image_dimensions(from_file)
        all_ok &= check("File path width capped at 400", w2 == 400, f"got {w2}")
    finally:
        os.unlink(tmp_path)

    # Image already smaller than max_width — should not upscale
    small = make_test_image(200, 150)
    small_c = compress(small, max_width=800, quality=90)
    w3, h3 = image_dimensions(small_c)
    all_ok &= check("Small image not upscaled", w3 == 200, f"got {w3}")

    # ── 3. Decompress ─────────────────────────────────────────────────────────
    print("\n[3] Decompress...")
    data, dw, dh, dmime = decompress(compressed)
    all_ok &= check("Decompress returns bytes",  isinstance(data, bytes))
    all_ok &= check("Mime is image/jpeg",        dmime == "image/jpeg")
    all_ok &= check("Width returned",            dw == 800, f"got {dw}")
    all_ok &= check("Height returned",           dh == 600, f"got {dh}")

    # Decompress PNG (should convert to JPEG)
    from PIL import Image as PILImage
    import io
    png_buf = io.BytesIO()
    PILImage.new("RGB", (100, 80), (255, 0, 0)).save(png_buf, format="PNG")
    png_bytes = png_buf.getvalue()
    data2, w2, h2, mime2 = decompress(png_bytes)
    all_ok &= check("PNG converted to JPEG",     mime2 == "image/jpeg")
    all_ok &= check("PNG dimensions preserved",  w2 == 100 and h2 == 80, f"{w2}x{h2}")

    # ── 4. image_to_field / field_to_display ──────────────────────────────────
    print("\n[4] Field round-trip...")
    field_bytes = image_to_field(original, max_width=800, quality=70)
    all_ok &= check("image_to_field returns bytes",  isinstance(field_bytes, bytes))
    all_ok &= check("Field bytes > 0",               len(field_bytes) > 0)

    display = field_to_display(field_bytes)
    all_ok &= check("field_to_display returns dict",   isinstance(display, dict))
    all_ok &= check("Display has data key",            "data" in display)
    all_ok &= check("Display has width",               display["width"] == 800)
    all_ok &= check("Display has size_kb",             display["size_kb"] > 0,
                    f"{display['size_kb']} KB")

    # None inputs
    all_ok &= check("image_to_field(None) is None",  image_to_field(None) is None)
    all_ok &= check("field_to_display(None) is None", field_to_display(None) is None)
    all_ok &= check("field_to_display(b'') is None",  field_to_display(b"") is None)

    # ── 5. fits_in_packet ─────────────────────────────────────────────────────
    print("\n[5] fits_in_packet...")
    all_ok &= check("Tiny bytes fit",        fits_in_packet(b"\x00" * 100))
    all_ok &= check("Large bytes don't fit", not fits_in_packet(field_bytes))

    # ── 6. Error handling ─────────────────────────────────────────────────────
    print("\n[6] Error handling...")
    bad = image_to_field(b"\x00\x01\x02\x03not_an_image")
    all_ok &= check("Bad bytes returns None", bad is None)

    decomp_bad, bw, bh, bm = decompress(b"\x00\x01\x02\x03not_an_image")
    all_ok &= check("Bad decompress returns raw bytes",  decomp_bad == b"\x00\x01\x02\x03not_an_image")
    all_ok &= check("Bad decompress dims are 0",         bw == 0 and bh == 0)

    # ── 7. LXMF field integration ─────────────────────────────────────────────
    print("\n[7] LXMF field integration...")
    try:
        import LXMF
        field_val = image_to_field(original, max_width=640, quality=65)
        fields = {LXMF.FIELD_IMAGE: field_val}
        all_ok &= check("Stored in LXMF.FIELD_IMAGE key",
                        LXMF.FIELD_IMAGE in fields and len(fields[LXMF.FIELD_IMAGE]) > 0)
        recovered = field_to_display(fields[LXMF.FIELD_IMAGE])
        all_ok &= check("Recovered from LXMF field", recovered is not None)
        all_ok &= check("Width 640", recovered["width"] == 640, f"got {recovered['width']}")
        print(f"  {INFO} Image field size: {len(field_val):,} bytes ({field_val[:3].hex()}...)")
    except ImportError:
        print(f"  {INFO} LXMF not available in this environment, skipping field test")

    print("\n" + "-" * 40)
    if all_ok:
        print(f"{PASS} All Phase 3 checks passed!\n")
        return 0
    else:
        print(f"{FAIL} Some checks failed - see above.\n")
        return 1


if __name__ == "__main__":
    sys.exit(run())
