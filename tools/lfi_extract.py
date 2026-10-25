#!/usr/bin/env python3
"""Pull the firmware's own files out of a logical NOR image.

The LFI is a 512-byte header, a directory of 32-byte entries, then the files, each aligned to a
cache line. This reads that directory and writes every member out, which is how you get at
`CONFIG.BIN` (for tools/cfgdump.py) and `TTS_RC.LIB` (to see what prompts a unit really has)
without building anything.

Usage:
  python3 tools/lfi_extract.py                        # list the members of the published stock image
  python3 tools/lfi_extract.py IMAGE.bin OUT_DIR      # write them out
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from paths import asset  # noqa: E402

LFI_BASE = 0x4280      # LFI head offset inside a logical image
CACHE = 0x40


def ck4(b):
    return sum(struct.unpack(f"<{len(b) // 4}I", b)) & 0xFFFFFFFF


def members(image, lfi_base=LFI_BASE):
    """[(name, blob)] for every file in the image's LFI, in directory order."""
    out = []
    index = 0
    while True:
        entry = image[lfi_base + 512 + 32 * index:lfi_base + 512 + 32 * (index + 1)]
        if len(entry) < 32 or entry[:11] == b"\0" * 11:
            break
        offset, length = struct.unpack_from("<II", entry, 16)
        out.append((bytes(entry[:11]), image[lfi_base + offset:lfi_base + offset + length]))
        index += 1
    return out


def checksums(image, lfi_base=LFI_BASE):
    """[(name, stored_checksum, computed_checksum)] — all three equal on an intact image."""
    out = []
    index = 0
    for name, blob in members(image, lfi_base):
        entry = image[lfi_base + 512 + 32 * index:lfi_base + 512 + 32 * (index + 1)]
        out.append((name, struct.unpack_from("<I", entry, 28)[0], ck4(blob)))
        index += 1
    return out


def filename(name):
    """b'TTS_RC  LIB' -> 'TTS_RC.LIB'"""
    stem, ext = name[:8].decode("latin1").strip(), name[8:11].decode("latin1").strip()
    return f"{stem}.{ext}" if ext else stem


def main(argv):
    image_path = argv[0] if argv else str(asset("firmware", "stock", "nor_logical_1MB.bin"))
    image = open(image_path, "rb").read()
    out_dir = argv[1] if len(argv) > 1 else None
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    print(f"{image_path}: {len(members(image))} files")
    for (name, blob), (_, stored, computed) in zip(members(image), checksums(image)):
        mark = "ok" if stored == computed else "CHECKSUM MISMATCH"
        print(f"  {filename(name):14s} {len(blob):8d} B  {mark}")
        if out_dir:
            open(os.path.join(out_dir, filename(name)), "wb").write(blob)
    if out_dir:
        print(f"written to {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
