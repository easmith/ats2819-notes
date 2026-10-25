#!/usr/bin/env python3
"""Read-only: collect everything the boot ROM read command (CBW 0xCD/0x93) can reach.

Nothing is uploaded and nothing is written: no ADFUS, no FWSC, no 0x10 FLASH commands.
Order is least to most risky; the first failing region stops the run (an unmapped address
can hang the ROM; unplug/replug recovers). Output goes to dumps/extract/.

  1. SPI NOR XIP window 0x10000000 (1 MB) - compared with dumps/flash_full.bin
  2. SPI0 / cache-controller registers that BREC programs (RXDAT and TXDAT skipped)
  3. boot ROM + on-chip SRAM, 0xbfc00000 upwards, until the first read error
     (SRAM may still hold the kernel's LFI head/dir copy: ADFU is entered from running firmware)

Usage: sudo python3 tools/extract_all.py [--mmu]   (--mmu: only read the MMU/SPI0 register block)
"""
import os
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from adfu_core import open_adfu, P, UsbLink  # noqa: E402
from paths import (BUILD_ROOT, REPO_ROOT, VENDOR_ROOT, asset, image_or_shipped, out_dir, vendor)  # noqa: E402


OUT = str(BUILD_ROOT / "dumps" / "extract")
CHUNK = 4096

# (address, note); words only, 4-byte reads. 0xc0110008 TXDAT / 0xc011000c RXDAT are FIFOs.
REGISTERS = [
    (0xc0110000, "SPI0 CTL"), (0xc0110004, "SPI0 STATUS"), (0xc0110010, "SPI0 BC"),
    (0xc0110014, "SPI0 +14 (FWSC writes chipid/key here)"), (0xc0110018, "SPI0 +18"),
    (0xc011001c, "SPI0 +1c"), (0xc0110020, "SPI0 +20 (BREC copies to c00a0094)"),
    (0xc0110024, "SPI0 +24"),
    (0xc00a0080, "c00a0080"), (0xc00a0084, "c00a0084"), (0xc00a0088, "c00a0088"),
    (0xc00a008c, "c00a008c"), (0xc00a0090, "c00a0090 (BREC writes 0x41000000)"),
    (0xc00a0094, "c00a0094 (BREC: = SPI0+20)"), (0xc00a0098, "c00a0098"),
    (0xc00a009c, "c00a009c"),
    (0xc012001c, "c012001c (FWSC sets bit 0)"),
]


def save(name, data):
    path = os.path.abspath(os.path.join(OUT, name))
    open(path, "wb").write(data)
    print(f"  saved {len(data)} bytes to {path}")


def read_region(rom, addr, size, name):
    data = b""
    t0 = time.time()
    try:
        while len(data) < size:
            data += rom.read_ram(addr + len(data), min(CHUNK, size - len(data)))
            print(f"\r  0x{addr + len(data):08x}", end="", flush=True)
        print()
        ok = True
    except Exception as e:
        print(f"\n  stopped at 0x{addr + len(data):08x}: {e!r}")
        ok = False
    save(name, data)
    print(f"  {time.time() - t0:.1f}s")
    return ok


def read_mmu(rom):
    """MMU block: entry i = (c00a0090+8i mapping_addr, c00a0094+8i phy_addr), see kernel hal_mmu."""
    lines = []
    for addr in list(range(0xc00a0000, 0xc00a0140, 4)) + list(range(0xc0110000, 0xc0110040, 4)):
        if addr in (0xc0110008, 0xc011000c):    # TX/RX FIFOs
            continue
        v = struct.unpack("<I", rom.read_ram(addr, 4, chunk=4))[0]
        note = ""
        if 0xc00a0090 <= addr < 0xc00a0110:
            i, w = divmod(addr - 0xc00a0090, 8)
            note = f"  entry {i} {'mapping' if w == 0 else 'phy'}"
        lines.append(f"0x{addr:08x} = 0x{v:08x}{note}")
        print(" ", lines[-1])
    open(os.path.join(OUT, "registers_full.txt"), "w").write("\n".join(lines) + "\n")


def main():
    out_dir("dumps", "extract")
    dev = open_adfu()
    rom = P.RomSession(UsbLink.autodetect(dev))
    print("boot ROM inquiry:", rom.inquiry())
    if "--mmu" in sys.argv:
        read_mmu(rom)
        print("done; unplug and replug the headphones to leave download mode")
        return

    print("1) XIP window 0x10000000")
    if not read_region(rom, 0x10000000, 0x100000, "xip_0x10000000.bin"):
        return
    old = os.path.join(str(BUILD_ROOT / "dumps"), "flash_full.bin")
    if os.path.exists(old):
        same = open(old, "rb").read()[:0x100000] == open(os.path.join(OUT, "xip_0x10000000.bin"), "rb").read()
        print("  identical to build/dumps/flash_full.bin:", same)

    print("2) registers")
    lines = []
    for addr, note in REGISTERS:
        try:
            v = struct.unpack("<I", rom.read_ram(addr, 4, chunk=4))[0]
            line = f"0x{addr:08x} = 0x{v:08x}  {note}"
        except Exception as e:
            line = f"0x{addr:08x}: read failed {e!r}  {note}"
            print(" ", line)
            lines.append(line)
            break
        print(" ", line)
        lines.append(line)
    open(os.path.join(OUT, "registers.txt"), "w").write("\n".join(lines) + "\n")

    print("3) boot ROM + SRAM from 0xbfc00000")
    read_region(rom, 0xbfc00000, 0x80000, "kseg1_0xbfc00000.bin")
    print("done; unplug and replug the headphones to leave download mode")


if __name__ == "__main__":
    main()
