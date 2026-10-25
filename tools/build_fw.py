#!/usr/bin/env python3
"""Build UPGRADE.HEX for the B18 headset from its own recovered NOR image with a new TTS_RC.LIB.

Everything runs on the host; nothing is sent to the device. Inputs:
  firmware/stock/nor_logical_1MB.bin   decrypted device NOR (MBREC, BREC, LFI head+dir+files)
  firmware/prompts-ru-en/tts_rc.lib        the replacement prompts (ru+en)
  vendor/afi/    SDK NorHwsc.bin, NorFwsc.bin, nor_id.bin (XOR 0x38 already removed)
  vendor/2819p/psp_rel/bin/upgrade.bin
  reference/fwu/US282A_UPGRADE.HEX + .keybuf   (FWU v3 crypto header, reused by tools/fwu_pack.py)

See notes/FWU_FORMAT.md for the reverse-engineered format.

UPGRADE.HEX is the disk-upgrade path, and it is NOT the safer one. The package carries its
own FWSC loader and runs it, so an unpatched package bricks the device exactly the way a
bad ADFU write does, and it offers no read-back check. The recommended path is
tools/adfu_flash.py, which writes and verifies inside one USB session. See docs/en/06-flashing.md.
"""
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import fwu_pack  # noqa: E402
from paths import (BUILD_ROOT, REPO_ROOT, VENDOR_ROOT, asset, image_or_shipped, out_dir, vendor)  # noqa: E402


NOR = str(REPO_ROOT / "firmware" / "stock" / "nor_logical_1MB.bin")
# the prompt library: a freshly built one if there is one, else the published build
AFI_BASE = str(VENDOR_ROOT / "afi")      # scripts/bootstrap.sh decodes the SDK loaders here
OUT = str(BUILD_ROOT / "fw")

LFI_BASE = 0x4280          # LFI head offset inside the NOR image
MBREC = (0x0000, 0x0400)
BREC = (0x0FC0, 0x4280)
HWSC_ADDR = 0xBFC2C000     # NorHwsc.bin load address: same slot as FWSC, run sequentially (disasm)
FWSC_ADDR = 0xBFC2C000     # NorFwsc.bin load address (disasm; init entry +0x200)
CACHE = 0x40
TTS_NAME = b"TTS_RC  LIB"


def ck2(b):
    return sum(struct.unpack(f"<{len(b) // 2}H", b)) & 0xFFFF


def ck4(b):
    return sum(struct.unpack(f"<{len(b) // 4}I", b)) & 0xFFFFFFFF


def align(n, a=CACHE):
    return (n + a - 1) // a * a


def file_83(name):
    return name.ljust(11, b"\0")[:11]


def rebuild_lfi(nor, new_tts):
    """Return (lfi_bytes, lfi_cap). Replaces TTS_RC.LIB; shifts the files after it."""
    head = bytearray(nor[LFI_BASE:LFI_BASE + 512])
    entries = []
    i = 0
    while True:
        e = bytearray(nor[LFI_BASE + 512 + 32 * i:LFI_BASE + 512 + 32 * (i + 1)])
        if e[:11] == b"\0" * 11:
            break
        entries.append(e)
        i += 1
    dir_len = 512 + align(32 * (len(entries) + 1))   # last dir slot stays empty (fwsp)

    data = {}
    for e in entries:
        off, ln = struct.unpack_from("<II", e, 16)
        data[bytes(e[:11])] = nor[LFI_BASE + off:LFI_BASE + off + ln]
    new_tts = new_tts + b"\0" * (-len(new_tts) % CACHE)
    data[TTS_NAME] = new_tts

    offset = dir_len
    dir_ck = 0
    for e in entries:
        name = bytes(e[:11])
        blob = data[name]
        struct.pack_into("<I", e, 16, offset)       # offset
        struct.pack_into("<I", e, 20, len(blob))    # length (cache-aligned)
        struct.pack_into("<I", e, 28, ck4(blob))    # checksum over the stored bytes
        dir_ck = (dir_ck + ck4(e)) & 0xFFFFFFFF
        offset += len(blob)

    lfi_cap = align(offset) + CACHE                 # fwsp: aligned size + one cache line
    buf = bytearray(lfi_cap)
    buf[:512] = head
    for n, e in enumerate(entries):
        buf[512 + 32 * n:512 + 32 * (n + 1)] = e
    off2 = {bytes(e[:11]): struct.unpack_from("<I", e, 16)[0] for e in entries}
    for name, blob in data.items():
        o = off2[name]
        buf[o:o + len(blob)] = blob

    struct.pack_into("<I", buf, 68 - 68 + 44, 0)    # no-op guard (keep layout explicit)
    struct.pack_into("<I", buf, 16, dir_ck)         # head.dir_checksum
    struct.pack_into("<H", buf, 510, ck2(bytes(buf[:510])))   # head.head_checksum
    return bytes(buf), lfi_cap


def rebuild_brec(nor, lfi_cap):
    brec = bytearray(nor[BREC[0]:BREC[1]])
    all_size = struct.unpack_from("<I", brec, 8)[0]
    struct.pack_into("<I", brec, 20, lfi_cap)                 # lfi used bytes
    struct.pack_into("<H", brec, 2, ck2(bytes(brec[4:0x80]))) # head checksum (0x80-byte head)
    struct.pack_into("<H", brec, 0, (ck2(bytes(brec[2:all_size])) + 0x1234) & 0xFFFF)
    return bytes(brec)


def build_afi(items, boot_config, lfi_cap, fwsc):
    """items: list of (name, type, address, data). FWSC header patched here."""
    head = bytearray(4096)
    head[:3] = b"AFI"
    body = bytearray()
    dirs = []
    off = 4096
    for name, typ, addr, data in items:
        d = bytearray(32)
        d[:11] = file_83(name)
        d[11] = typ
        struct.pack_into("<I", d, 12, addr)
        struct.pack_into("<I", d, 16, off)
        struct.pack_into("<I", d, 20, len(data))
        struct.pack_into("<I", d, 24, 0)              # subtype (upgrade.bin ignores it)
        struct.pack_into("<I", d, 28, ck4(data + b"\0" * (-len(data) % 4)))
        dirs.append(d)
        body += data
        off += len(data)
    for n, d in enumerate(dirs):
        head[32 + 32 * n:32 + 32 * (n + 1)] = d
    struct.pack_into("<I", head, 16, off)             # length
    struct.pack_into("<I", head, 4092, ck4(bytes(head[:4092])))
    afi = bytes(head) + bytes(body)
    return afi + b"\0" * (-len(afi) % 512)


def patch_fwsc(fwsc, lfi_cap, boot_config):
    h = bytearray(fwsc)
    h[2] = 0                                   # operate (0 = program)
    struct.pack_into("<I", h, 8, lfi_cap)      # lfi_cap
    struct.pack_into("<I", h, 12, 0xF0000)     # spi cap
    h[16] = 0                                  # spi_decrypt = 0 (device NOR not encrypted)
    struct.pack_into("<I", h, 20, 0)
    struct.pack_into("<I", h, 24, 0)
    h[28] = 1                                  # upgrade flag
    # +29 bonding, +48 FF*16, +112 nor_id (filled by upgrade.bin), +240 chipid=0
    h[48:64] = b"\xff" * 16
    h[240:304] = b"\0" * 64
    h[384:448] = boot_config
    return bytes(h)


def main():
    os.makedirs(OUT, exist_ok=True)
    nor = open(NOR, "rb").read()
    new_tts = open(image_or_shipped("tts_rc.lib"), "rb").read()
    brec_dev = nor[BREC[0]:BREC[1]]
    boot_config = brec_dev[128:192]
    bonding = brec_dev[192:196]

    lfi, lfi_cap = rebuild_lfi(nor, new_tts)
    print(f"LFI: {len(lfi)} bytes, lfi_cap 0x{lfi_cap:x} (VRAM at 0xF0000, margin 0x{0xF0000 - lfi_cap:x})")
    assert lfi_cap <= 0xF0000, "LFI overflows into VRAM"

    brec = rebuild_brec(nor, lfi_cap)
    mbrec = nor[MBREC[0]:MBREC[1]]
    hwsc = open(vendor("afi", "NorHwsc.bin"), "rb").read()
    fwsc = patch_fwsc(open(vendor("afi", "NorFwsc.bin"), "rb").read(), lfi_cap, boot_config)
    fwsc = bytearray(fwsc)
    fwsc[29:33] = bonding
    fwsc = bytes(fwsc)
    nor_id = open(vendor("2819p", "case", "fwpkg", "drv", "nor_id.bin"), "rb").read()

    items = [
        (b"NORHWSC BIN", 0x48, HWSC_ADDR, hwsc),
        (b"NORFWSC BIN", 0x46, FWSC_ADDR, fwsc),
        (b"NOR_ID  BIN", 0x60, 0, nor_id),
        (b"NORBREC BIN", 0x42, 0, brec),
        (b"FWIMAGE FW ", 0x49, 0, lfi),
        (b"NORMBRECBIN", 0x4D, 0, mbrec),
    ]
    afi = build_afi(items, boot_config, lfi_cap, fwsc)
    open(os.path.join(OUT, "afi_plain.bin"), "wb").write(afi)
    print(f"AFI: {len(afi)} bytes, {len(items)} entries")

    ref = open(vendor("fwu", "US282A_UPGRADE.HEX"), "rb").read()
    keybuf = open(vendor("fwu", "US282A_UPGRADE.keybuf"), "rb").read()
    fwu = fwu_pack.pack(ref, keybuf, afi, tail=True)
    out = open(vendor("2819p", "psp_rel", "bin", "upgrade.bin"), "rb").read() + fwu
    path = os.path.join(OUT, "UPGRADE.HEX")
    open(path, "wb").write(out)
    print(f"UPGRADE.HEX: {len(out)} bytes -> {path}")
    print("NOTE: flashing this from the device disk runs the FWSC inside the package and has\n"
          "      no read-back check. tools/adfu_flash.py is the path that was verified.")


if __name__ == "__main__":
    main()
