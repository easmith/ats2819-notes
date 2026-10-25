#!/usr/bin/env python3
"""Run a tiny MIPS32 stub through the boot ROM to read 32-bit registers.

Why: the ROM RAM command (CBW 0xCD/0x13, 0x93) copies byte by byte (lbu/sb, ROM 0xbfc03c31),
so peripheral registers read through it are garbage. A stub does real `lw` reads.

Flow (no flash access; the stub only reads registers and writes RAM):
  1. write zeros to the result buffer 0xbfc29000, upload the stub to 0xbfc28000 (where the
     vendor tool puts ADFUS; ROM stack is at 0xbfc45ff0 down, ROM state at 0xbfc20100), read back
  2. CBW 0xCD/0x20 run 0xbfc28000. The ROM jumps there with `jr a0` and does not return
     (0xbfc040ed -> 0xbfc000c8)
  3. the stub copies the MMU block 0xc00a0000..0xc00a013c and SPI0 regs to the buffer, writes a
     magic word, and jumps back into the ROM ADFU loop 0xbfc040ed. That loop re-inits USB and
     clears the run flag (0xbfc0324d), so the device re-enumerates as 10d6:10d6
  4. reconnect and read the buffer back with 0x93 (RAM, so byte copies are fine)

  map0: the stub only sets free MMU entry 9 (mapping 0x42000000 = valid | virtual 0x02000000,
        phy 0 = NOR offset 0), exactly what the ROM does for boot (0xbfc014b5). The host then
        reads virtual 0x02000000 with 0x93. Entry 1 maps 0x10000000 -> NOR 0x5980, so
        bytes 0x5980.. of the result equal the start of a previous full read, if you have one.

  rawread <phy> <len>: NorFwsc init + its raw SPI read (ops[0] 0xbfc2d065) of physical NOR bytes,
        CRC words included; never calls the +0x400 driver. Saves raw_0x<phy>_<len>.bin.
  mapat <phy> <len>: same as map0 but entry 9 -> NOR offset <phy>; saves nor_0x<phy>.bin.

Usage: sudo python3 tools/adfu_stub.py regs|map0|hwsc|fwsc|readbuf|mapat <phy> <len>
"""
import os
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from adfu_core import open_adfu, P, UsbLink, wait_for_rom  # noqa: E402
from paths import (BUILD_ROOT, REPO_ROOT, VENDOR_ROOT, asset, image_or_shipped, out_dir, vendor)  # noqa: E402


STUB_ADDR = 0xbfc28000
BUF_ADDR = 0xbfc29000
ADFU_LOOP = 0xbfc040ed          # MIPS16 entry of the ROM ADFU command loop
MAGIC = 0x600DF00D
OUT = str(BUILD_ROOT / "dumps" / "extract2")

ZERO, T0, T1, T2, T3, T9 = 0, 8, 9, 10, 11, 25
SPI0_OFFSETS = [0x00, 0x04, 0x10, 0x14, 0x18, 0x1c, 0x20, 0x24, 0x28, 0x2c, 0x30, 0x34, 0x38, 0x3c]


def i_type(op, rs, rt, imm):
    return (op << 26) | (rs << 21) | (rt << 16) | (imm & 0xFFFF)


def lui(rt, imm): return i_type(0x0F, 0, rt, imm)
def ori(rt, rs, imm): return i_type(0x0D, rs, rt, imm)
def addiu(rt, rs, imm): return i_type(0x09, rs, rt, imm)
def lw(rt, off, rs): return i_type(0x23, rs, rt, off)
def sw(rt, off, rs): return i_type(0x2B, rs, rt, off)
def jr(rs): return (rs << 21) | 0x08
def jalr(rs): return (rs << 21) | (31 << 11) | 0x09
def bgtz(rs, off): return i_type(0x07, rs, 0, off)
NOP = 0
T4, T5, V0, A0, A1, A2, A3 = 12, 13, 2, 4, 5, 6, 7
HWSC_ENTRY = 0xBFC30000
FWSC_ENTRY = 0xBFC2C200


def li32(rt, value):
    return [lui(rt, value >> 16), ori(rt, rt, value & 0xFFFF)]


def regs_stub():
    code = []
    code += li32(T0, 0xc00a0000)
    code += li32(T1, BUF_ADDR)
    code += [addiu(T2, ZERO, 0x140 // 4)]
    loop = len(code)
    code += [lw(T3, 0, T0), addiu(T0, T0, 4), sw(T3, 0, T1), addiu(T2, T2, -1)]
    branch_at = len(code)
    code += [i_type(0x05, T2, ZERO, loop - (branch_at + 1)), addiu(T1, T1, 4)]   # bne + delay slot
    code += li32(T0, 0xc0110000)
    for i, off in enumerate(SPI0_OFFSETS):
        code += [lw(T3, off, T0), sw(T3, 4 * i, T1)]
    code += li32(T3, MAGIC) + [sw(T3, 4 * len(SPI0_OFFSETS), T1)]
    code += li32(T9, ADFU_LOOP) + [jr(T9), NOP]
    return struct.pack(f"<{len(code)}I", *code)


MAP_ENTRY = 9                    # free on this device (regs run, 2026-10-25)
MAP_VIRT = 0x02000000
MAP_LEN = 0x10000


def map0_stub(phy=0):
    code = []
    code += li32(T0, 0xc00a0090 + 8 * MAP_ENTRY)
    code += li32(T3, phy) + [sw(T3, 4, T0)]                     # phy = NOR offset
    code += li32(T3, 0x40000000 | MAP_VIRT) + [sw(T3, 0, T0)]   # mapping, valid bit last
    code += [lw(T3, 0, T0)]
    code += li32(T1, BUF_ADDR) + [sw(T3, 0, T1)]
    code += [lw(T3, 4, T0), sw(T3, 4, T1)]
    code += li32(T3, MAGIC) + [sw(T3, 8, T1)]
    code += li32(T9, ADFU_LOOP) + [jr(T9), NOP]
    return struct.pack(f"<{len(code)}I", *code)


def do_mapat(phy, length):
    """Map NOR offset `phy` at virtual 0x02000000 (free entry 9) and read `length` bytes."""
    rom, words = run_stub(map0_stub(phy), 3)
    print(f"entry {MAP_ENTRY}: mapping 0x{words[0]:08x}, phy 0x{words[1]:08x}")
    data = b""
    try:
        while len(data) < length:
            data += rom.read_ram(MAP_VIRT + len(data), min(4096, length - len(data)))
    except Exception as e:
        print(f"read stopped at 0x{MAP_VIRT + len(data):08x}: {e!r}")
    path = os.path.join(OUT, f"nor_0x{phy:06x}.bin")
    open(path, "wb").write(data)
    print(f"saved {len(data)} bytes to {os.path.abspath(path)}")
    for off in range(0, len(data), 0x10000):
        blk = data[off:off + 0x10000]
        print(f"  0x{phy + off:06x}: non-FF {sum(b != 0xFF for b in blk)}, non-00 {sum(b != 0 for b in blk)}")
    print("done; unplug and replug the headphones to leave download mode")


def _call_and_copy(entry, len_slot, dst_slot):
    """Zero a0-a3, jalr entry (returns v0=ptr{buf,len}), copy len bytes to dst_slot."""
    code = [addiu(A0, ZERO, 0), addiu(A1, ZERO, 0), addiu(A2, ZERO, 0), addiu(A3, ZERO, 0)]
    code += li32(T9, entry) + [jalr(T9), NOP]
    code += [lw(T0, 0, V0), lw(T1, 4, V0)]
    code += li32(T2, BUF_ADDR + len_slot) + [sw(T1, 0, T2)]
    code += li32(T2, BUF_ADDR + dst_slot) + [addiu(T3, T0, 0)]
    loop = len(code)
    code += [lw(T4, 0, T3), sw(T4, 0, T2), addiu(T3, T3, 4), addiu(T2, T2, 4), addiu(T1, T1, -4)]
    code += [bgtz(T1, loop - (len(code) + 1)), NOP]
    return code


def _restore_regs():
    """Write back the SPI0/MMU registers HWSC/FWSC change, so XIP works and the ROM loop survives.
    Values are the live ones captured by `regs` (build/dumps/extract2/registers_lw.txt)."""
    import re
    txt = open(os.path.join(OUT, "registers_lw.txt")).read()
    vals = {int(a, 16): int(v, 16) for a, v in re.findall(r"(0x[0-9a-f]+) = (0x[0-9a-f]+)", txt)}
    code = []
    for e in range(16):                         # 16 MMU pairs: phy first, then mapping (valid bit)
        m, p = 0xc00a0090 + 8 * e, 0xc00a0094 + 8 * e
        code += li32(T2, p) + li32(T3, vals.get(p, 0)) + [sw(T3, 0, T2)]
        code += li32(T2, m) + li32(T3, vals.get(m, 0)) + [sw(T3, 0, T2)]
    for off in (0x00, 0x10, 0x14, 0x18, 0x1c, 0x20):   # SPI0 (skip 0x04 status, read-only)
        a = 0xc0110000 + off
        code += li32(T2, a) + li32(T3, vals.get(a, 0)) + [sw(T3, 0, T2)]
    return code


LOADER_ADDR = 0xBFC2C000     # HWSC and FWSC both load here, run sequentially (disasm)
HWSC_CALL = 0xBFC2C000       # HWSC entry = base
FWSC_CALL = 0xBFC2C200       # FWSC init entry = base + 0x200


def call_stub(entry):
    """One pass: call `entry`, copy its 36-byte status to BUF+8 (len at BUF+4),
    restore XIP/SPI0 regs, write magic, return to the ROM ADFU loop."""
    code = _call_and_copy(entry, 4, 8)
    code += _restore_regs()
    code += li32(T2, BUF_ADDR) + li32(T3, MAGIC) + [sw(T3, 0, T2)]
    code += li32(T9, ADFU_LOOP) + [jr(T9), NOP]
    return struct.pack(f"<{len(code)}I", *code)


def reconnect(timeout=15):
    d = wait_for_rom(timeout)
    if d is None:
        sys.exit("device did not come back in download mode; unplug/replug")
    d.set_configuration()
    return P.RomSession(UsbLink.autodetect(d))


def run_stub(stub, n_words):
    rom = P.RomSession(UsbLink.autodetect(open_adfu()))
    print("boot ROM inquiry:", rom.inquiry())
    rom.write_ram(BUF_ADDR, bytes(4 * n_words))
    rom.write_ram(STUB_ADDR, stub)
    if rom.read_ram(STUB_ADDR, len(stub)) != stub:
        sys.exit("stub read-back mismatch; not running it")
    print(f"stub {len(stub)} bytes at 0x{STUB_ADDR:08x} verified; running")
    rom.run(STUB_ADDR)
    time.sleep(1.0)
    for attempt in range(5):
        try:
            rom = reconnect()
            words = struct.unpack(f"<{n_words}I", rom.read_ram(BUF_ADDR, 4 * n_words))
            break
        except Exception as e:
            print(f"read-back attempt {attempt + 1} failed: {e!r}; retrying")
            time.sleep(2)
    else:
        sys.exit("could not read the result; unplug/replug the headphones")
    if words[-1] != MAGIC:
        sys.exit(f"magic missing (0x{words[-1]:08x}): stub did not run to the end")
    return rom, words


def do_map0():
    rom, words = run_stub(map0_stub(), 3)
    print(f"entry {MAP_ENTRY}: mapping 0x{words[0]:08x}, phy 0x{words[1]:08x}")
    data = b""
    try:
        while len(data) < MAP_LEN:
            data += rom.read_ram(MAP_VIRT + len(data), 4096)
    except Exception as e:
        print(f"read stopped at 0x{MAP_VIRT + len(data):08x}: {e!r}")
    path = os.path.join(OUT, "nor_0x000000.bin")
    open(path, "wb").write(data)
    print(f"saved {len(data)} bytes to {os.path.abspath(path)}")
    old = os.path.join(str(BUILD_ROOT / "dumps"), "flash_full.bin")
    if len(data) > 0x5980 and os.path.exists(old):
        ref = open(old, "rb").read()[:len(data) - 0x5980]
        print("bytes 0x5980.. equal flash_full.bin start:", data[0x5980:] == ref)
    print("head:", data[:32].hex(" "))
    print("done; unplug and replug the headphones to leave download mode")


def _run_pass(loader, entry, label, stub=None, extra=None):
    """Upload `loader` to 0xbfc2c000 and a one-call stub, run it, read the 36-byte status back.
    Returns (ok, status_bytes). Each pass re-enters the ROM ADFU loop (regs restored).
    extra=(addr, n): also read n bytes at addr after the pass; returned as a third value."""
    stub = stub or call_stub(entry)
    rom = P.RomSession(UsbLink.autodetect(open_adfu()))
    print(f"[{label}] boot ROM inquiry:", rom.inquiry())
    rom.write_ram(BUF_ADDR, bytes(0x100))
    rom.write_ram(LOADER_ADDR, bytes(loader))
    rom.write_ram(STUB_ADDR, stub)
    if rom.read_ram(STUB_ADDR, len(stub)) != stub:
        sys.exit("stub read-back mismatch; not running it")
    print(f"[{label}] loader {len(loader)} B @0x{LOADER_ADDR:08x}, entry 0x{entry:08x}, stub {len(stub)} B; running")
    rom.run(STUB_ADDR)
    import usb.util
    usb.util.dispose_resources(rom.link.dev)
    time.sleep(1.5)
    for attempt in range(6):
        try:
            rom = reconnect()
            raw = rom.read_ram(BUF_ADDR, 0x100)
            more = rom.read_ram(*extra) if extra else None
            break
        except Exception as e:
            print(f"[{label}] read-back attempt {attempt + 1}: {e!r}; retrying")
            time.sleep(2)
    else:
        sys.exit(f"[{label}] could not read the result; unplug/replug the headphones")
    global last_raw
    last_raw = raw
    magic, slen = struct.unpack_from("<II", raw, 0)
    if magic != MAGIC:
        print(f"[{label}] magic 0x{magic:08x}: stub did not finish (loader hung at 0x{entry:08x})")
        return (False, b"", None) if extra else (False, b"")
    return (True, raw[8:8 + 36], more) if extra else (True, raw[8:8 + 36])


def _loaders():
    nor = open(asset("firmware", "stock", "nor_logical_1MB.bin"), "rb").read()
    brec = nor[0xFC0:0x4280]
    chipinfo, bonding, boot_config = brec[64:128], brec[192:196], brec[128:192]
    import build_fw
    hwsc = open(vendor("afi", "NorHwsc.bin"), "rb").read()
    fwsc = bytearray(build_fw.patch_fwsc(open(vendor("afi", "NorFwsc.bin"), "rb").read(), 0, boot_config))
    fwsc[29:33] = bonding
    fwsc[112:112 + 64] = chipinfo          # upgrade.bin fills this from the nor_id match at runtime
    return hwsc, bytes(fwsc)


def do_hwsc():
    hwsc, _ = _loaders()
    ok, st = _run_pass(hwsc, HWSC_CALL, "HWSC")
    if ok:
        print("HWSC status:", st.hex(" "))
        print("  flash_id (status+20):", st[20:24].hex(), "expected c84015 (GD25Q16)")
        open(os.path.join(OUT, "probe_hwsc.txt"), "w").write(st.hex(" ") + "\n")
    print("done; unplug and replug the headphones to leave download mode")


def do_fwsc():
    _, fwsc = _loaders()
    ok, st = _run_pass(fwsc, FWSC_CALL, "FWSC")
    if ok:
        print("FWSC init status:", st.hex(" "))
        print("  status word (+24):", struct.unpack_from("<H", st, 24)[0], "(0=ok, 1=no flash, 4=chipid mismatch); logic_cap(+20) 0x%x" % struct.unpack_from("<I", st, 20)[0])
        open(os.path.join(OUT, "probe_fwsc.txt"), "w").write(st.hex(" ") + "\n")
    print("done; unplug and replug the headphones to leave download mode")


FWSC_RAW_READ = 0xBFC2D065     # NorFwsc ops[0]: read(phys addr, buf, len), MIPS16, cmd 0x0B, no 64->66
RAW_BUF = BUF_ADDR + 0x100
RAW_MAX = LOADER_ADDR - RAW_BUF
last_raw = b""


def rawread_stub(phy, length):
    """FWSC init (safe, never the +0x400 driver), then raw SPI read of physical NOR bytes."""
    mark = lambda v: li32(T2, BUF_ADDR + 0x80) + li32(T3, v) + [sw(T3, 0, T2)]   # progress at BUF+0x80
    code = mark(0x11110000)
    code += _call_and_copy(FWSC_CALL, 4, 8)
    code += mark(0x22220000)
    code += li32(A0, phy) + li32(A1, RAW_BUF) + li32(A2, length)
    code += li32(T9, FWSC_RAW_READ) + [jalr(T9), NOP]
    code += mark(0x33330000)
    code += _restore_regs()
    code += li32(T2, BUF_ADDR) + li32(T3, MAGIC) + [sw(T3, 0, T2)]
    code += li32(T9, ADFU_LOOP) + [jr(T9), NOP]
    return struct.pack(f"<{len(code)}I", *code)


def do_rawread(phy, length):
    assert 0 < length <= RAW_MAX and phy + length <= 0x200000
    _, fwsc = _loaders()
    ok, st, data = _run_pass(fwsc, FWSC_CALL, "RAW", stub=rawread_stub(phy, length), extra=(RAW_BUF, length))
    if not ok:
        print("progress marker at BUF+0x80 (0x1111 start, 0x2222 init done, 0x3333 read done):",
              f"0x{struct.unpack('<I', last_raw[0x80:0x84])[0]:08x}, init len {struct.unpack('<I', last_raw[4:8])[0]}")
        return
    print("FWSC init status word:", struct.unpack_from("<H", st, 24)[0])
    path = os.path.join(OUT, f"raw_0x{phy:06x}_{length:x}.bin")
    open(path, "wb").write(data)
    print(f"saved {len(data)} raw bytes from NOR 0x{phy:x} to {os.path.abspath(path)}")
    print("head:", data[:32].hex(" "))
    print("done; unplug and replug the headphones to leave download mode")


def decode_probe(raw):
    magic, hlen = struct.unpack_from("<II", raw, 0)
    hstat, flen, fstat = raw[8:44], struct.unpack_from("<I", raw, 48)[0], raw[52:88]
    if magic != MAGIC:
        print(f"magic 0x{magic:08x} (expected 0x{MAGIC:08x}): the stub did not finish — a loader hung")
        return
    print(f"HWSC returned len={hlen}\n  status: {hstat.hex(' ')}")
    print("  flash_id (status+20):", hstat[20:24].hex(), "expected c84015 (GD25Q16)")
    print(f"FWSC init returned len={flen}\n  status: {fstat.hex(' ')}")
    print("  status word:", struct.unpack_from("<H", fstat, 0)[0], "(0=ok, 1=no flash, 4=chipid mismatch)")
    open(os.path.join(OUT, "probe.txt"), "w").write(
        f"hwsc len {hlen}\n{hstat.hex(' ')}\nfwsc len {flen}\n{fstat.hex(' ')}\n")


def do_readbuf():
    """Read the 128-byte probe result left in RAM at BUF_ADDR (fresh process, read-only)."""
    rom = P.RomSession(UsbLink.autodetect(open_adfu()))
    print("boot ROM inquiry:", rom.inquiry())
    raw = rom.read_ram(BUF_ADDR, 128)
    decode_probe(raw)


def main():
    out_dir("dumps", "extract2")
    if len(sys.argv) == 4 and sys.argv[1] == "rawread":
        return do_rawread(int(sys.argv[2], 0), int(sys.argv[3], 0))
    if len(sys.argv) == 4 and sys.argv[1] == "mapat":
        return do_mapat(int(sys.argv[2], 0), int(sys.argv[3], 0))
    if sys.argv[1:] not in (["regs"], ["map0"], ["hwsc"], ["fwsc"], ["readbuf"]):
        sys.exit(__doc__)
    if sys.argv[1] == "map0":
        return do_map0()
    if sys.argv[1] == "hwsc":
        return do_hwsc()
    if sys.argv[1] == "fwsc":
        return do_fwsc()
    if sys.argv[1] == "readbuf":
        return do_readbuf()
    stub = regs_stub()
    n_words = 0x140 // 4 + len(SPI0_OFFSETS) + 1

    rom = P.RomSession(UsbLink.autodetect(open_adfu()))
    print("boot ROM inquiry:", rom.inquiry())
    rom.write_ram(BUF_ADDR, bytes(4 * n_words))
    rom.write_ram(STUB_ADDR, stub)
    if rom.read_ram(STUB_ADDR, len(stub)) != stub:
        sys.exit("stub read-back mismatch; not running it")
    print(f"stub {len(stub)} bytes at 0x{STUB_ADDR:08x} verified; running")
    rom.run(STUB_ADDR)
    time.sleep(1.0)

    for attempt in range(5):
        try:
            rom = reconnect()
            raw = rom.read_ram(BUF_ADDR, 4 * n_words)
            break
        except Exception as e:
            print(f"read-back attempt {attempt + 1} failed: {e!r}; retrying")
            time.sleep(2)
    else:
        sys.exit("could not read the result; unplug/replug the headphones")
    words = struct.unpack(f"<{n_words}I", raw)
    if words[-1] != MAGIC:
        sys.exit(f"magic missing (0x{words[-1]:08x}): stub did not run to the end")
    lines = []
    for i in range(0x140 // 4):
        addr = 0xc00a0000 + 4 * i
        note = ""
        if 0xc00a0090 <= addr < 0xc00a0110:
            e, w = divmod(addr - 0xc00a0090, 8)
            note = f"  entry {e} {'mapping' if w == 0 else 'phy'}"
        lines.append(f"0x{addr:08x} = 0x{words[i]:08x}{note}")
    for i, off in enumerate(SPI0_OFFSETS):
        lines.append(f"0x{0xc0110000 + off:08x} = 0x{words[0x50 + i]:08x}  SPI0 +0x{off:02x}")
    text = "\n".join(lines) + "\n"
    print(text)
    open(os.path.join(OUT, "registers_lw.txt"), "w").write(text)
    print("done; unplug and replug the headphones to leave download mode")


if __name__ == "__main__":
    main()
