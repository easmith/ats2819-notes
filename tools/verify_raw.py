#!/usr/bin/env python3
"""Check a raw NOR read-back (tools/adfu_flash.py --read) against the expected layout.

NorFwsc stores every 64-byte logical line as a 66-byte physical line that depends only on the
line content (verified on the device: 43 repeated lines, 0 conflicts). So a raw dump of a known
write is a dictionary plain line -> raw line. The reference is the raw read of the first
(SDK-layout) flash of the stock image: MBREC @0, BREC @0x400, LFI @0x400+len(BREC).

Usage:
  python3 tools/verify_raw.py NEW_RAW [--layout device|sdk] [--mbrec] [--ref REF_RAW] [--image stock|ours]
    device layout = MBREC @0, BREC @0xFC0, LFI @0x4280 (what this device's MBREC expects)
    --mbrec       MBREC was written (otherwise lines 0..62 must still be erased, all FF)
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import build_fw  # noqa: E402
from paths import (BUILD_ROOT, REPO_ROOT, VENDOR_ROOT, asset, image_or_shipped, out_dir, vendor)  # noqa: E402


# The published reference: a raw read of a working device, in this device's own layout.
DEFAULT_REF = REPO_ROOT / "firmware" / "raw" / "raw_first_1MB.bin"

LINE, PLINE = 64, 66
ERASED = b"\xff" * PLINE


def image_parts(which):
    nor = open(build_fw.NOR, "rb").read()
    mbrec, brec = nor[0:0x400], nor[0xFC0:0x4280]
    if which == "stock":
        lfi_cap = int.from_bytes(brec[20:24], "little")
        return mbrec, brec, nor[0x4280:0x4280 + lfi_cap]
    import adfu_flash
    _, _, brec, lfi, mbrec = adfu_flash.load_image("ours")
    return mbrec, brec, lfi


def layout(parts, kind, n_lines, with_mbrec=True):
    """List of expected plain lines (None = must be erased) for the first n_lines."""
    mbrec, brec, lfi = parts
    out = [None] * n_lines
    brec_at = 0x400 if kind == "sdk" else 0xFC0
    lfi_at = brec_at + len(brec)
    for off, blob in ((0, mbrec if with_mbrec else b""), (brec_at, brec), (lfi_at, lfi)):
        for i in range(len(blob) // LINE):
            n = off // LINE + i
            if n < n_lines:
                out[n] = blob[i * LINE:(i + 1) * LINE]
    return out


def lines(raw):
    return [raw[i * PLINE:(i + 1) * PLINE] for i in range(len(raw) // PLINE)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("new")
    ap.add_argument("--ref", default=str(DEFAULT_REF))
    ap.add_argument("--ref-layout", choices=["device", "sdk"], default="device",
                    help="layout the reference itself was written in")
    ap.add_argument("--layout", choices=["device", "sdk"], default="device")
    ap.add_argument("--mbrec", action="store_true")
    ap.add_argument("--image", choices=["stock", "ours"], default="stock")
    a = ap.parse_args()

    ref = lines(open(a.ref, "rb").read())
    stock = image_parts("stock")
    table = {}
    for p, r in zip(layout(stock, a.ref_layout, len(ref)), ref):
        if p is not None:
            assert table.setdefault(p, r) == r, "reference is not a function of line content"

    new = lines(open(a.new, "rb").read())
    want = layout(image_parts(a.image), a.layout, len(new), a.mbrec)
    ok = bad = unknown = 0
    first_bad = []
    own = {}                                  # lines the reference lacks: must be self-consistent
    for n, (p, r) in enumerate(zip(want, new)):
        exp = ERASED if p is None else table.get(p)
        if exp is None and (r == ERASED or own.setdefault(p, r) != r):
            exp = b""                         # erased where data belongs, or same plain -> other raw
        if exp is None:
            unknown += 1
        elif exp == r:
            ok += 1
        else:
            bad += 1
            if len(first_bad) < 10:
                first_bad.append(n)
    print(f"{a.new}: {len(new)} lines, layout {a.layout}, image {a.image}, mbrec {'yes' if a.mbrec else 'no'}")
    print(f"  match {ok}, MISMATCH {bad}, not in reference but self-consistent {unknown}")
    if first_bad:
        print("  first mismatching lines:", ", ".join(f"{n} (phys 0x{n * PLINE:x})" for n in first_bad))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
