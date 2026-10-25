#!/usr/bin/env python3
"""Flash the B18 headset NOR over ADFU, replaying the vendor production flow (fwsp.py DoADFU).

Flow (build/eats2819/afi_py/Production.py + fwsp.py, loaders RE'd in notes/RE_NOTES.md):
  1. boot ROM: upload the vendor ADFUS.BIN to 0xbfc28000 and SWITCH (0x20) to it
  2. ADFUS 0x13/0x21/0xA3: upload NorHwsc.bin to 0xbfc2c000, CALL, GET_STATUS -> flash id
  3. ADFUS: upload NorFwsc.bin (header patched like FWSC_Data) to 0xbfc2c000,
     CALL +0x200 (init), GET_STATUS -> status must be 0
     ---- everything above is read-only; the dry run (default) stops here ----
  4. 0x10 mode 0x46 BREC    (first driver call: NorFwsc erases NOR 0x0000-0xFFFF)
  5. 0x10 mode 0xC0 LFI
  6. 0x10 mode 0x40 MBREC   (written last, like the vendor tool and upgrade.bin)
  7. 0x10 mode 0xFF over

CBW layout (same as the ROM): cb[0]=0xCD, cb[1]=sub (|0x80 for IN), cb[5:9]=len, cb[9:13]=addr;
for 0x10 the mode is addr>>24.

Images:
  ours   firmware/prompts-ru-en/afi_plain.bin (tools/build_fw.py): BREC/FWIMAGE/MBREC with the new TTS_RC.LIB
  stock  firmware/stock/nor_logical_1MB.bin: the device's own MBREC/BREC/LFI, to restore factory state

Usage:
  sudo python3 tools/adfu_flash.py ours            # dry run: loaders only, no flash write
  sudo python3 tools/adfu_flash.py stock --read 0 0x2e00 --masks 0,0x2000   # raw SPI read, no write
  sudo python3 tools/adfu_flash.py stock --write   # real write (asks for confirmation)
"""
import argparse
import os
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from adfu_core import open_adfu, P, UsbLink  # noqa: E402
import build_fw  # noqa: E402
import loader_check  # noqa: E402
from paths import (BUILD_ROOT, REPO_ROOT, VENDOR_ROOT, asset, image_or_shipped, out_dir, vendor)  # noqa: E402


ROOT = build_fw.ROOT
ADFUS_ADDR = 0xBFC28000
LOADER_ADDR = 0xBFC2C000         # HWSC and FWSC share this slot (proven by tools/adfu_stub.py hwsc/fwsc)
FWSC_INIT = LOADER_ADDR + 0x200
GD25Q16_ID = bytes.fromhex("c84015")
USB_CHUNK = 4096                 # bulk transfers > 4 KB stalled on this Mac
CSW_TIMEOUT = 120000             # ms; the first LFI call may erase the whole region


class Adfu:
    def __init__(self, dev):
        self.dev = dev
        self.link = UsbLink.autodetect(dev)
        self.tag = 1

    def _cbw(self, sub, length, addr, direction_in):
        self.tag += 1
        cb = bytearray(16)
        cb[0], cb[1] = 0xCD, sub
        struct.pack_into("<II", cb, 5, length, addr)
        return P.cbw(self.tag, length, direction_in, cb)

    def _csw(self, timeout=5000):
        st = P.parse_csw(bytes(self.dev.read(self.link.ep_in, 13, timeout=timeout)))
        if st != 0:
            raise IOError(f"CSW status {st}")

    def drain(self):
        """Discard replies left in the IN pipe by an earlier exchange."""
        import usb.core
        try:
            while True:
                print("drained:", bytes(self.dev.read(self.link.ep_in, 512, timeout=200)).hex())
        except usb.core.USBError:
            pass

    def inquiry(self):
        self.tag += 1
        self.link.write(P.rom_inquiry_cbw(self.tag))
        data = self.link.read(11)
        self._csw()
        return data

    def write_ram(self, addr, blob):
        for off in range(0, len(blob), USB_CHUNK):
            part = blob[off:off + USB_CHUNK]
            self.link.write(self._cbw(0x13, len(part), addr + off, False))
            self.link.write(part)
            self._csw()

    def read_ram(self, addr, length):
        out = b""
        for off in range(0, length, USB_CHUNK):
            n = min(USB_CHUNK, length - off)
            self.link.write(self._cbw(0x93, n, addr + off, True))
            out += self.link.read(n)
            self._csw()
        return out

    def switch(self, addr):
        self.link.write(self._cbw(0x20, 0, addr, False))
        self._csw()

    def call(self, addr, timeout=30000):
        self.link.write(self._cbw(0x21, 0, addr, False))
        self._csw(timeout)

    def status(self, length):
        self.link.write(self._cbw(0xA3, length, 0, True))
        data = self.link.read(length)
        self._csw()
        return data

    def flash(self, mode, offset, data, label, timeout=CSW_TIMEOUT):
        """0x10 OUT: ADFUS receives the data in 8 KB pieces and calls drv(mode, addr, n, buf).
        One command per section, like fwsp.py (it splits only above 8 MB)."""
        assert mode & 0x40 and offset < 0x1000000 and len(data) <= 0x800000
        self.link.write(self._cbw(0x10, len(data), (mode << 24) | offset, False))
        for off in range(0, len(data), USB_CHUNK):
            self.dev.write(self.link.ep_out, data[off:off + USB_CHUNK], timeout=timeout)
            done = off + len(data[off:off + USB_CHUNK])
            if done % 0x10000 == 0 or done == len(data):
                print(f"\r{label} {done:#x} / {len(data):#x}", end="", flush=True)
        self._csw(timeout)
        print(" ok", flush=True)

    def over(self):
        self.link.write(self._cbw(0x10, 0x200, 0xFF000000, False))
        self.link.write(bytes(0x200))
        self._csw(CSW_TIMEOUT)


def nor_record(chip_id):
    """128-byte NOR_ID record matching chip_id (0xFF = wildcard), as SEARCH_NOR_ID does."""
    tbl = open(vendor("2819p", "case", "fwpkg", "drv", "nor_id.bin"), "rb").read()
    want = chip_id.ljust(4, b"\0")
    for off in range(0, len(tbl) - 127, 128):
        sid = tbl[off:off + 4]
        if all(s in (0xFF, w) for s, w in zip(sid, want)):
            return tbl[off:off + 128]
    sys.exit(f"flash id {chip_id.hex()} not in nor_id.bin")


def afi_entries(afi):
    out = {}
    for n in range(15):
        d = afi[32 + 32 * n:64 + 32 * n]
        if not d[0]:
            break
        addr, off, length = struct.unpack_from("<III", d, 12)
        out[d[:11].decode().replace(" ", "")] = (addr, afi[off:off + length])
    return out


BREC_OFF_SITES = (0xBFC2F534, 0xBFC2F556, 0xBFC2F5D6)   # NorFwsc BREC write: addiu rx, 1024


def fix_brec_offset(fwsc):
    """SDK NorFwsc writes BREC at logical 0x400 (+1024) and the LFI after it; this device's MBREC
    (dual-bank build) looks for BREC at logical 0xFC0. Re-encode the three EXTEND'ed addiu
    immediates 1024 -> 0xFC0 (EXTEND f400 -> f7c1). RE_NOTES "Post-flash no-boot analysis"."""
    b = bytearray(fwsc)
    for va in BREC_OFF_SITES:
        o = va - LOADER_ADDR
        if not (b[o:o + 2] == b"\x00\xf4" and b[o + 3] in (0x48, 0x49)):
            print(f"NOT patching {va:#x}: unexpected code there; BREC will be written at the SDK's "
                  "0x400, which this device's MBREC does not look at", file=sys.stderr)
            continue
        b[o:o + 2] = (0xF7C1).to_bytes(2, "little")
    return bytes(b)


HW_SEED = [True]                 # set False by --sw-seed, for comparison runs
SEED_SEL_SITE = 0xBFC2C308       # ori t9,v1,0x4000 in NorFwsc init: CTL |= RAND_SEL


def use_hw_seed(fwsc):
    """Make NorFwsc write with the chip's own (eFuse) randomizer seed instead of a software one.

    NorFwsc init at 0xbfc2c2b8.. does:  CTL |= 0x4000 (RAND_SEL = take the seed from SPI0+0x14),
    then SPI0+0x14 = fwsc_head[240 + idx*4], idx = fwsc_head[380] % 35. That table is the vendor
    script's `chipid_data` (afi_py/fwsp.py:997 and :1250-1260), where the real CHIPID|package_id is
    hidden at a random index -- i.e. the randomizer seed IS the chip's CHIPID, which is what
    INF_SPINOR_ENCRYPT=1 means. The shipped loader's table is all zeros, so our first flash encoded
    the whole image with seed 0, while the boot ROM reads with the eFuse seed -- hence the garbage
    and the cache CRC errors. Measured on the device: reading with RAND_SEL set and seed 0 returns
    the factory image byte-exactly; reading with RAND_SEL clear returns 0xFF with CRC errors.

    Clearing just this one bit makes the write engine use the same seed source as the ROM's reads.
    The seed-register store and its read-back check are left alone, so init still returns status 0.
    """
    o = SEED_SEL_SITE - LOADER_ADDR
    b = bytearray(fwsc)
    if struct.unpack_from("<I", b, o)[0] != 0x34794000:
        print(f"NOT patching {SEED_SEL_SITE:#x}: unexpected code there; the write would use a "
              "software seed and the boot ROM would not be able to read the result", file=sys.stderr)
        return bytes(b)
    struct.pack_into("<I", b, o, 0x34790000)          # ori t9,v1,0 -> CTL written back unchanged
    print(f"NorFwsc patched at {SEED_SEL_SITE:#x}: RAND_SEL not set, write uses the eFuse seed")
    return bytes(b)


def load_image(which):
    """Returns (hwsc, fwsc, brec, lfi, mbrec). FWSC header is patched except the NOR record."""
    hwsc = open(vendor("afi", "NorHwsc.bin"), "rb").read()
    loader_check.warn(loader_check.check_norfwsc(open(vendor("afi", "NorFwsc.bin"), "rb").read()))
    if which == "ours":
        e = afi_entries(open(image_or_shipped("afi_plain.bin"), "rb").read())
        brec, lfi, mbrec, fwsc = e["NORBRECBIN"][1], e["FWIMAGEFW"][1], e["NORMBRECBIN"][1], e["NORFWSCBIN"][1]
        if e["NORHWSCBIN"][1] != hwsc:
            sys.exit("afi_plain.bin HWSC differs from vendor/afi/NorHwsc.bin")
    else:
        nor = open(build_fw.NOR, "rb").read()
        brec = nor[build_fw.BREC[0]:build_fw.BREC[1]]
        mbrec = nor[build_fw.MBREC[0]:build_fw.MBREC[1]]
        lfi_cap = struct.unpack_from("<I", brec, 20)[0]
        lfi = nor[build_fw.LFI_BASE:build_fw.LFI_BASE + lfi_cap]
        fwsc = bytearray(build_fw.patch_fwsc(open(vendor("afi", "NorFwsc.bin"), "rb").read(),
                                             lfi_cap, brec[128:192]))
        fwsc[29:33] = brec[192:196]
        fwsc = bytes(fwsc)
    fwsc = fix_brec_offset(fwsc)
    if HW_SEED[0]:
        fwsc = use_hw_seed(fwsc)
    check_image(brec, lfi, mbrec, fwsc)
    return hwsc, fwsc, brec, lfi, mbrec


def check_image(brec, lfi, mbrec, fwsc):
    ck2 = build_fw.ck2
    all_size = struct.unpack_from("<I", brec, 8)[0]
    assert struct.unpack_from("<H", brec, 2)[0] == ck2(brec[4:0x80]), "BREC head checksum"
    assert struct.unpack_from("<H", brec, 0)[0] == (ck2(brec[2:all_size]) + 0x1234) & 0xFFFF, "BREC checksum"
    lfi_cap = struct.unpack_from("<I", brec, 20)[0]
    assert len(lfi) == lfi_cap and struct.unpack_from("<I", fwsc, 8)[0] == lfi_cap, "lfi_cap mismatch"
    assert struct.unpack_from("<H", lfi, 510)[0] == ck2(lfi[:510]), "LFI head checksum"
    assert len(mbrec) == 0x400 and mbrec[2] == 0x5A, "MBREC"
    assert fwsc[2] == 0 and fwsc[16] == 0, "FWSC operate/decrypt must be 0"
    assert build_fw.LFI_BASE + lfi_cap <= 0xF0000, "LFI reaches VRAM"
    print(f"image ok: BREC {len(brec)} B, LFI {lfi_cap:#x} B (ends at NOR {build_fw.LFI_BASE + lfi_cap:#x}), MBREC {len(mbrec)} B")


def start_adfus():
    adfu = Adfu(open_adfu())
    loader = open(vendor("afi", "ADFUS.BIN"), "rb").read()
    last = None
    for attempt in range(6):
        # ADFU entered by a software switch from the running firmware needs a moment to settle,
        # and the first CBW after the switch often comes back with CSW status 2. Drain and retry.
        try:
            print("boot ROM inquiry:", adfu.inquiry())
            last = None
            break
        except Exception as e:
            last = e
            print(f"  inquiry attempt {attempt + 1} failed ({e!r}); draining and retrying")
            try:
                adfu.drain()
            except Exception:
                pass
            time.sleep(0.4)
    if last is not None:
        e = last
        # ADFUS answers the 0xCC inquiry differently from the ROM, so a failure here usually means
        # a previous run left ADFUS running. Re-uploading it would overwrite the code we are
        # executing from, so check for it and reuse it instead.
        adfu.drain()
        try:
            already = adfu.read_ram(ADFUS_ADDR, len(loader)) == loader
        except Exception:
            already = False
        if not already:
            sys.exit(f"device did not answer the ROM inquiry ({e!r}) and ADFUS is not loaded;\n"
                     f"unplug and replug the headphones, then run this again")
        print("inquiry refused, but ADFUS is already resident and answering: reusing it")
        return adfu
    adfu.write_ram(ADFUS_ADDR, loader)
    if adfu.read_ram(ADFUS_ADDR, len(loader)) != loader:
        sys.exit("ADFUS read-back mismatch; nothing run")
    print(f"ADFUS {len(loader)} B at {ADFUS_ADDR:#x}; switching")
    adfu.switch(ADFUS_ADDR)
    time.sleep(0.3)                  # vendor: 100 ms, no inquiry (ADFUS answers 0xCC differently)
    adfu.drain()
    # ADFUS keeps the ROM's connection; prove it is alive with its own 0x93 RAM read
    if adfu.read_ram(ADFUS_ADDR, len(loader)) != loader:
        sys.exit("ADFUS RAM read-back mismatch after SWITCH; nothing written")
    print("ADFUS running (RAM read through ADFUS ok)")
    return adfu


def run_loaders(adfu, hwsc, fwsc):
    adfu.write_ram(LOADER_ADDR, hwsc)
    if adfu.read_ram(LOADER_ADDR, len(hwsc)) != hwsc:
        sys.exit("HWSC read-back mismatch")
    adfu.call(LOADER_ADDR)
    st = adfu.status(36)
    chip = st[20:23]
    print("HWSC status:", st.hex(" "), "-> flash id", chip.hex())
    if st[16:20] != bytes(4) or chip != GD25Q16_ID:
        sys.exit(f"unexpected flash id {st[16:24].hex()}, expected 00000000 c84015..")
    fwsc = bytearray(fwsc)
    fwsc[112:240] = nor_record(chip)                 # what SEARCH_NOR_ID/FWSC_Data put there
    adfu.write_ram(LOADER_ADDR, bytes(fwsc))
    if adfu.read_ram(LOADER_ADDR, len(fwsc)) != bytes(fwsc):
        sys.exit("FWSC read-back mismatch")
    adfu.call(FWSC_INIT)
    st = adfu.status(36)
    status, = struct.unpack_from("<H", st, 24)
    print("FWSC init status:", st.hex(" "), f"-> status {status}, logic_cap {struct.unpack_from('<I', st, 20)[0]:#x}")
    if status != 0:
        sys.exit("FWSC init failed (1 = no flash, 4 = chipid mismatch); nothing written")


def write_flash(adfu, brec, lfi, mbrec, with_mbrec=True):
    t0 = time.time()
    adfu.flash(0x46, 0, brec, "BREC (first call erases NOR 0x0000-0xFFFF)")
    adfu.flash(0xC0, 0, lfi, "LFI")
    if not with_mbrec:
        print(f"BREC+LFI written in {time.time() - t0:.1f}s; MBREC NOT written (device stays in ADFU)")
        return
    adfu.flash(0x40, 0, mbrec, "MBREC")
    adfu.over()
    print(f"written in {time.time() - t0:.1f}s")


FWSC_RAW_READ = 0xBFC2D065      # NorFwsc ops[0]: read(phys addr, buf, len), MIPS16, cmd 0x0B, raw bytes
READ_STUB = 0xBFC34000          # above NorFwsc (+BSS), far below the stack
READ_BUF = 0xBFC35000
READ_MAX = 0x4000


def read_stub(phy, n_lines, mask):
    """MIPS32 function for ADFUS CALL (0x21): reads n_lines 66-byte physical lines with the raw read.
    Before each line it clears and re-sets `mask` in SPI0 CTL (the write path toggles 0x2000 per line
    and keeps 0x8000 on, RE_NOTES "Post-flash no-boot analysis"); mask 0 = plain raw read."""
    from adfu_stub import li32, lw, sw, addiu, jalr, jr, NOP, A0, A1, A2, T9, T0, T1, T2
    SP, RA, S0, S1, S2, S3 = 29, 31, 16, 17, 18, 19
    def r3(rd, rs, rt, fn): return (rs << 21) | (rt << 16) | (rd << 11) | fn
    AND = lambda rd, rs, rt: r3(rd, rs, rt, 0x24)
    OR = lambda rd, rs, rt: r3(rd, rs, rt, 0x25)
    code = [addiu(SP, SP, -48), sw(RA, 44, SP), sw(S0, 40, SP), sw(S1, 36, SP), sw(S2, 32, SP), sw(S3, 28, SP)]
    code += li32(S0, phy) + li32(S1, READ_BUF) + li32(S2, n_lines) + li32(S3, 0xC0110000)
    loop = len(code)
    code += [lw(T0, 0, S3)] + li32(T1, ~mask & 0xFFFFFFFF) + [AND(T0, T0, T1), sw(T0, 0, S3)]
    code += li32(T2, mask) + [OR(T0, T0, T2), sw(T0, 0, S3)]
    code += [addiu(A0, S0, 0), addiu(A1, S1, 0), addiu(A2, 0, 66)]
    code += li32(T9, FWSC_RAW_READ) + [jalr(T9), NOP]
    code += [addiu(S0, S0, 66), addiu(S1, S1, 66), addiu(S2, S2, -1)]
    br = len(code)
    code += [(0x07 << 26) | (S2 << 21) | ((loop - (br + 1)) & 0xFFFF), NOP]       # bgtz s2, loop
    code += [lw(T0, 0, S3)] + li32(T1, ~mask & 0xFFFFFFFF) + [AND(T0, T0, T1), sw(T0, 0, S3)]
    code += [lw(S3, 28, SP), lw(S2, 32, SP), lw(S1, 36, SP), lw(S0, 40, SP), lw(RA, 44, SP),
             addiu(SP, SP, 48), jr(RA), NOP]
    return struct.pack(f"<{len(code)}I", *code)


def raw_read(adfu, phy, length, mask=0):
    """Raw-read whole 66-byte lines from physical `phy`, in calls of up to READ_MAX bytes."""
    n_lines = -(-length // 66)
    per_call = READ_MAX // 66
    assert phy + n_lines * 66 <= 0x200000
    data = b""
    for first in range(0, n_lines, per_call):
        n = min(per_call, n_lines - first)
        stub = read_stub(phy + first * 66, n, mask)
        adfu.write_ram(READ_BUF, b"\xA5" * (n * 66))     # pattern, so a no-op read is visible
        adfu.write_ram(READ_STUB, stub)
        if adfu.read_ram(READ_STUB, len(stub)) != stub:
            sys.exit("read stub read-back mismatch")
        adfu.call(READ_STUB)
        data += adfu.read_ram(READ_BUF, n * 66)
        print(f"\rread 0x{len(data):x} / 0x{n_lines * 66:x}", end="", flush=True)
    print()
    path = os.path.join(out_dir("dumps"), f"raw_0x{phy:06x}_{len(data):x}_m{mask:x}_{time.strftime('%H%M%S')}.bin")
    open(path, "wb").write(data)
    mb = open(build_fw.NOR, "rb").read()[:0x400]
    hits = sum(data[i * 66:i * 66 + 64] == mb[i * 64:i * 64 + 64] for i in range(min(16, n_lines))) if phy == 0 else "-"
    print(f"mask 0x{mask:04x}: {path}\n  head {data[:16].hex(' ')}  MBREC lines matching plain: {hits}/16"
          f"  pattern lines left: {sum(data[i:i + 66] == b'\xA5' * 66 for i in range(0, len(data), 66))}")
    return data


XIP_STUB = 0xBFC34800           # MMU-entry setter, called through ADFUS (read-only)
XIP_VIRT = 0x02000000
XIP_ENTRY = 0                   # FWSC init already maps 0x42000000 here; reuse it (regs dump 2026-10-25)
XIP_CTL = 0x38                  # SPI0 CTL in the state where the line/CRC decode worked (dumps/extract/registers.txt)


def xip_stub(logical, ctl=XIP_CTL, spi18=None, spi20=None, pokes=(), after=()):
    """MIPS32 function for ADFUS CALL: point free MMU entry 9 at NOR logical offset `logical`.

    The MMU phy field is a LOGICAL offset (proven: warm entry 1 phy 0x5980 == image offset 0x5980),
    so the controller does the logical->physical 64/66 line translation and the CRC check itself.
    Reading through this window is exactly what the CPU does when it runs code from flash."""
    from adfu_stub import li32, sw, jr, NOP, T0, T3
    RA = 31
    code = []
    for addr, val in pokes:                                      # arbitrary register writes, in order
        code += li32(T0, addr) + li32(T3, val) + [sw(T3, 0, T0)]
    code += li32(T0, 0xC0110000)
    if spi18 is not None:
        code += li32(T3, spi18) + [sw(T3, 0x18, T0)]             # SPI0 +18 as the firmware had it
    if spi20 is not None:
        code += li32(T3, spi20) + [sw(T3, 0x20, T0)]             # SPI0 +20 as the firmware had it
    code += li32(T3, ctl) + [sw(T3, 0, T0)]                      # SPI0 CTL = working value
    for addr, val in after:                                      # writes that must follow CTL
        code += li32(T0, addr) + li32(T3, val) + [sw(T3, 0, T0)]
    code += li32(T0, 0xC00A0090 + 8 * XIP_ENTRY)
    code += li32(T3, logical) + [sw(T3, 4, T0)]                 # phy (logical offset) first
    code += li32(T3, 0x40000000 | XIP_VIRT) + [sw(T3, 0, T0)]   # mapping, valid bit last
    code += [jr(RA), NOP]
    return struct.pack(f"<{len(code)}I", *code)


def xip_read(adfu, logical, length, ctl=XIP_CTL, spi18=None, spi20=None, pokes=(), after=()):
    """Read `length` bytes of NOR through the hardware line/CRC decode path. Read-only."""
    stub = xip_stub(logical, ctl, spi18, spi20, pokes, after)
    adfu.write_ram(XIP_STUB, stub)
    if adfu.read_ram(XIP_STUB, len(stub)) != stub:
        sys.exit("xip stub read-back mismatch")
    adfu.call(XIP_STUB)
    st0 = spi_status(adfu)
    data = b""
    try:
        while len(data) < length:
            data += adfu.read_ram(XIP_VIRT + len(data), min(4096, length - len(data)))
            print(f"\rxip read 0x{len(data):x} / 0x{length:x}", end="", flush=True)
    except Exception as e:
        print(f"\nread stopped at window +0x{len(data):x}: {e!r}")
    print()
    path = os.path.join(out_dir("dumps"), f"xip_0x{logical:06x}_{len(data):x}_{time.strftime('%H%M%S')}.bin")
    open(path, "wb").write(data)
    st1 = spi_status(adfu)
    # SPI0 STATUS bit 7 = cache CRC error (SDK hal_mmu_is_cache_crc_error reads 0xC0110004 & 0x80)
    print(f"  SPI0 STATUS before 0x{st0:08x}, after 0x{st1:08x}"
          f"  -> cache CRC error: {'YES' if st1 & 0x80 else 'no'}")
    want = open(build_fw.NOR, "rb").read()[logical:logical + len(data)]
    same = sum(a == b for a, b in zip(data, want))
    print(f"{path}\n  head {data[:16].hex(' ')}\n  want {want[:16].hex(' ')}"
          f"\n  matches factory image: {same}/{len(data)} bytes"
          f"{' -- IDENTICAL' if data == want else ''}")
    return data


REG_BLOCKS = [(0xC0110000, 16), (0xC00A0000, 80), (0xC0120000, 16), (0xC0140000, 16)]


def regs_stub(base, nwords):
    """MIPS32 function for ADFUS CALL: copy `nwords` words from `base` into READ_BUF. Read-only."""
    from adfu_stub import li32, lw, sw, addiu, jr, NOP, T0, T1, T2, T3
    RA = 31
    code = li32(T0, base) + li32(T1, READ_BUF) + [addiu(T2, 0, nwords)]
    loop = len(code)
    code += [lw(T3, 0, T0), sw(T3, 0, T1), addiu(T0, T0, 4), addiu(T1, T1, 4), addiu(T2, T2, -1)]
    br = len(code)
    code += [(0x07 << 26) | (T2 << 21) | ((loop - (br + 1)) & 0xFFFF), NOP]   # bgtz t2, loop
    code += [jr(RA), NOP]
    return struct.pack(f"<{len(code)}I", *code)


def spi_status(adfu):
    """Read SPI0 STATUS (0xC0110004). Bit 7 is the cache CRC error flag."""
    stub = regs_stub(0xC0110004, 1)
    adfu.write_ram(READ_STUB + 0x400, stub)
    adfu.call(READ_STUB + 0x400)
    return struct.unpack("<I", adfu.read_ram(READ_BUF, 4))[0]


def dump_regs(adfu):
    """Read the SPI0 / MMU / CMU register blocks in the current state. Read-only."""
    out = {}
    for base, n in REG_BLOCKS:
        stub = regs_stub(base, n)
        adfu.write_ram(READ_STUB, stub)
        if adfu.read_ram(READ_STUB, len(stub)) != stub:
            sys.exit("regs stub read-back mismatch")
        adfu.write_ram(READ_BUF, b"\xA5" * (4 * n))
        adfu.call(READ_STUB)
        blob = adfu.read_ram(READ_BUF, 4 * n)
        for i in range(n):
            v, = struct.unpack_from("<I", blob, 4 * i)
            out[base + 4 * i] = v
    path = os.path.join(out_dir("dumps"), f"regs_adfus_{time.strftime('%H%M%S')}.txt")
    with open(path, "w") as f:
        for a, v in out.items():
            f.write(f"0x{a:08x} = 0x{v:08x}\n")
    print(f"saved {path}")
    for a, v in out.items():
        if v not in (0, 0xA5A5A5A5):
            print(f"  0x{a:08x} = 0x{v:08x}")
    return out


def expected_logical(mbrec, brec, lfi):
    """The logical image the device should read back after a write: MBREC, gap, BREC, LFI."""
    img = bytearray(b"\xff" * (build_fw.LFI_BASE + len(lfi)))
    img[0:len(mbrec)] = mbrec
    img[build_fw.BREC[0]:build_fw.BREC[0] + len(brec)] = brec
    img[build_fw.LFI_BASE:build_fw.LFI_BASE + len(lfi)] = lfi
    path = os.path.join(out_dir("fw"), "expected_logical.bin")
    open(path, "wb").write(bytes(img))
    print(f"expected logical image written to {path} ({len(img):#x} B)")
    return bytes(img)


def xip_init_stub(logical, final_ctl, clear_bypass=True, randomize=True, seed=None):
    """MIPS32 function for ADFUS CALL: the SDK's hal_spi_phy_init sequence, then map the window.

    Transcribed from vendor/2819p/psp_rel/HAL/lib/hal_spi_phy_init.o (literal pool: CMU_MEMCLKEN
    0xC0001064, CMU_SPICLK 0xC0001028, MRCR 0xC0000000, SPI0 0xC0110000/4, WD_CTL 0xC012001C,
    base CTL 0x00180008, cache ctl 0xC0140000, cache invalidate 0xC0140004).

    Two things the earlier --xip attempts got wrong, which is why they returned all 0xFF:
      * the SPI_REQ (CTL bit 8) handshake -- the cache cannot reach the flash while the CPU owns
        the FIFO, and it must be handed back with STATUS bit 8 clear;
      * 0xC0140000 bit 2, which NorFwsc init ORs in (hence 1 cold, 5 after init) and which selects
        the raw/bypass path; hal_spi_phy_init only ever sets bit 0.
    Every wait is bounded so the stub always returns and the device keeps answering USB.
    """
    from adfu_stub import li32, lw, sw, addiu, jr, NOP, T0, T1, T2, T3
    ZERO, RA, T4 = 0, 31, 12

    def r3(rd, rs, rt, fn): return (rs << 21) | (rt << 16) | (rd << 11) | fn
    AND = lambda rd, rs, rt: r3(rd, rs, rt, 0x24)
    OR = lambda rd, rs, rt: r3(rd, rs, rt, 0x25)
    ANDI = lambda rt, rs, imm: (0x0C << 26) | (rs << 21) | (rt << 16) | (imm & 0xFFFF)
    BNE = lambda rs, rt, off: (0x05 << 26) | (rs << 21) | (rt << 16) | (off & 0xFFFF)
    BEQ = lambda rs, rt, off: (0x04 << 26) | (rs << 21) | (rt << 16) | (off & 0xFFFF)

    c = []

    def wr(addr, val):
        c.extend(li32(T0, addr)); c.extend(li32(T1, val)); c.append(sw(T1, 0, T0))

    def setb(addr, mask):
        c.extend(li32(T0, addr)); c.append(lw(T1, 0, T0))
        c.extend(li32(T2, mask)); c.append(OR(T1, T1, T2)); c.append(sw(T1, 0, T0))

    def clrb(addr, mask):
        c.extend(li32(T0, addr)); c.append(lw(T1, 0, T0))
        c.extend(li32(T2, (~mask) & 0xFFFFFFFF)); c.append(AND(T1, T1, T2)); c.append(sw(T1, 0, T0))

    def delay(n=8):
        c.append(addiu(T3, ZERO, n))
        top = len(c)
        c.append(addiu(T3, T3, -1))
        c.append(BNE(T3, ZERO, top - (len(c) + 1))); c.append(NOP)

    def wait(addr, mask, want_set, limit=0x4000):
        """Spin until (*addr & mask) matches, but give up after `limit` tries."""
        c.extend(li32(T0, addr)); c.extend(li32(T4, limit))
        top = len(c)
        c.append(lw(T1, 0, T0)); c.append(ANDI(T1, T1, mask)); c.append(addiu(T4, T4, -1))
        skip = len(c)
        c.append((BNE if want_set else BEQ)(T1, ZERO, 2)); c.append(NOP)   # matched -> leave
        c.append(BNE(T4, ZERO, top - (len(c) + 1))); c.append(NOP)
        assert skip  # keep flake8 quiet about the unused name

    CTL, STA, CACHE, INVAL = 0xC0110000, 0xC0110004, 0xC0140000, 0xC0140004
    wr(0xC0001064, 0x001FFFFF); delay()
    wr(0xC0001028, 0x00000040); delay()
    clrb(0xC0000000, 0x0040); delay(); setb(0xC0000000, 0x0040); delay()
    clrb(0xC0000000, 0x4000); delay(); setb(0xC0000000, 0x4000); delay()
    setb(CTL, 0x100)                      # SPI_REQ: CPU takes the FIFO
    setb(0xC012001C, 1)                   # kick the watchdog, as the SDK does
    wait(STA, 0x100, True)                # SPI_READY
    wr(CTL, 0x00180008); delay()
    setb(CACHE, 1)
    c.extend(li32(T0, CACHE)); c.append(lw(T1, 0, T0)); c.append(lw(T1, 0, T0))
    delay()
    if clear_bypass:
        clrb(CACHE, 4)
    wr(INVAL, 1); wait(INVAL, 1, False)
    clrb(CTL, 0x20); setb(CTL, 0x20); clrb(CTL, 0x10); setb(CTL, 0x10)
    clrb(CTL, 0x2000)
    if randomize:
        setb(CTL, 0x1000); clrb(CTL, 0x8000)
    else:
        clrb(CTL, 0x1000)
    if seed is not None:
        # CTL bit 14 (RAND_SEL / RAND_SEED) = use the software seed in SPI0+0x14 instead of the
        # hardware default. NorFwsc init latches the FWSC header chipid word here the same way.
        setb(CTL, 0x4000)
        wr(0xC0110014, seed)
        final_ctl |= 0x4000
    wr(CTL, final_ctl)
    wr(STA, 0x80)                         # clear any stale cache-CRC-error flag
    c.extend(li32(T0, 0xC00A0090)); c.extend(li32(T1, logical)); c.append(sw(T1, 4, T0))
    c.extend(li32(T1, 0x40000000 | XIP_VIRT)); c.append(sw(T1, 0, T0))
    clrb(CTL, 0x100); wait(STA, 0x100, False)      # hand the FIFO back to the cache
    wr(INVAL, 1); wait(INVAL, 1, False)
    c.append(jr(RA)); c.append(NOP)
    return struct.pack(f"<{len(c)}I", *c)


def xip_read2(adfu, logical, length, final_ctl=0x00361838, clear_bypass=True, randomize=True,
              seed=None, expect=None):
    """Read NOR through the cache after a full hal_spi_phy_init-style setup. Read-only."""
    stub = xip_init_stub(logical, final_ctl, clear_bypass, randomize, seed)
    adfu.write_ram(XIP_STUB, stub)
    if adfu.read_ram(XIP_STUB, len(stub)) != stub:
        sys.exit("xip init stub read-back mismatch")
    adfu.call(XIP_STUB)
    data = b""
    try:
        while len(data) < length:
            data += adfu.read_ram(XIP_VIRT + len(data), min(4096, length - len(data)))
    except Exception as e:
        print(f"read stopped at window +0x{len(data):x}: {e!r}")
    st = spi_status(adfu)
    ref = open(build_fw.NOR, "rb").read() if expect is None else expect
    want = ref[logical:logical + len(data)]
    same = sum(a == b for a, b in zip(data, want))
    path = os.path.join(out_dir("dumps"), f"xip2_0x{logical:06x}_{len(data):x}_{time.strftime('%H%M%S')}.bin")
    open(path, "wb").write(data)
    print(f"ctl 0x{final_ctl:08x} bypass_cleared={clear_bypass} rand={randomize}"
          f" seed={'hw default' if seed is None else hex(seed)}")
    print(f"  STATUS after 0x{st:08x} -> cache CRC error: {'YES' if st & 0x80 else 'no'}"
          f", SPI_READY {'set' if st & 0x100 else 'clear'}")
    print(f"  head {data[:16].hex(' ')}\n  want {want[:16].hex(' ')}")
    print(f"  matches expected image: {same}/{len(data)}{'  -- IDENTICAL' if data == want else ''}")
    print(f"  {path}")
    return data


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image", choices=["ours", "stock"])
    ap.add_argument("--write", action="store_true", help="really erase and program the NOR")
    ap.add_argument("--read", nargs=2, metavar=("PHY", "LEN"), type=lambda v: int(v, 0),
                    help="after the loaders, raw-read physical NOR bytes (read only, never the driver)")
    ap.add_argument("--ctl", default=hex(XIP_CTL), help="SPI0 CTL value to set before --xip")
    ap.add_argument("--no-loaders", action="store_true",
                    help="with --regs/--xip: do not run HWSC/FWSC, keep the boot ROM's own NOR read setup")
    ap.add_argument("--xip2", nargs=2, metavar=("LOGICAL", "LEN"), type=lambda v: int(v, 0),
                    help="full hal_spi_phy_init setup, then read through the cache (read only)")
    ap.add_argument("--sw-seed", action="store_true",
                    help="do NOT patch NorFwsc: keep the software seed 0 (the state that bricked it)")
    ap.add_argument("--seed", default=None, help="with --xip2: software randomizer seed (sets CTL bit 14)")
    ap.add_argument("--keep-bypass", action="store_true", help="with --xip2: leave 0xC0140000 bit 2 set")
    ap.add_argument("--no-rand", action="store_true", help="with --xip2: leave RAND_RXEN clear")
    ap.add_argument("--poke-after", default="", dest="poke_after",
                    help="comma list addr=value register writes AFTER the CTL write (cache invalidate)")
    ap.add_argument("--poke", default="", help="comma list addr=value register writes before --xip")
    ap.add_argument("--spi18", default=None, help="value for SPI0+0x18 before --xip")
    ap.add_argument("--spi20", default=None, help="value for SPI0+0x20 before --xip")
    ap.add_argument("--regs", action="store_true", help="dump SPI0/MMU/CMU registers (read only)")
    ap.add_argument("--xip", nargs=2, metavar=("LOGICAL", "LEN"), type=lambda v: int(v, 0),
                    help="after the loaders, read NOR through the hardware line/CRC decode (read only)")
    ap.add_argument("--verify", nargs="?", const=0x5000, type=lambda v: int(v, 0), default=None,
                    help="with --write: acceptance-read the flash through the cache afterwards, "
                         "in the same session, and print PASS/FAIL")
    ap.add_argument("--no-mbrec", action="store_true",
                    help="with --write: write BREC+LFI only, so the device keeps booting into ADFU")
    ap.add_argument("--masks", default="0", help="comma list of SPI0 CTL masks to try for --read, e.g. 0,0x2000")
    a = ap.parse_args()
    HW_SEED[0] = not a.sw_seed
    if a.sw_seed:
        loader_check.warn_sw_seed()
    if a.write and (a.read or a.xip or a.xip2 or a.regs):
        sys.exit("--read/--xip/--regs and --write are exclusive")

    hwsc, fwsc, brec, lfi, mbrec = load_image(a.image)
    if a.write:
        print(f"\nThis ERASES and REPROGRAMS the headset NOR with the '{a.image}' image"
              f"{' (BREC+LFI only, no MBREC)' if a.no_mbrec else ''}.")
        if input("Type WRITE to continue: ").strip() != "WRITE":
            sys.exit("aborted, nothing sent")
    adfu = start_adfus()
    if a.no_loaders:
        print("skipping HWSC/FWSC: the controller keeps the read config the boot ROM set up")
    else:
        run_loaders(adfu, hwsc, fwsc)
    if a.read:
        for m in a.masks.split(","):
            raw_read(adfu, *a.read, mask=int(m, 0))
    if a.regs:
        dump_regs(adfu)
    if a.xip2:
        xip_read2(adfu, *a.xip2, final_ctl=int(a.ctl, 0) if a.ctl != hex(XIP_CTL) else 0x00361838,
                  clear_bypass=not a.keep_bypass, randomize=not a.no_rand,
                  seed=None if a.seed is None else int(a.seed, 0))
    if a.xip:
        xip_read(adfu, *a.xip, ctl=int(a.ctl, 0),
                 spi18=None if a.spi18 is None else int(a.spi18, 0),
                 spi20=None if a.spi20 is None else int(a.spi20, 0),
                 pokes=[tuple(int(x, 0) for x in kv.split("=")) for kv in a.poke.split(",") if kv],
                 after=[tuple(int(x, 0) for x in kv.split("=")) for kv in a.poke_after.split(",") if kv])
    if a.read or a.xip or a.xip2 or a.regs:
        print("Read only, nothing written. Unplug and replug the headphones.")
        return
    if a.write and a.verify:
        write_flash(adfu, brec, lfi, mbrec, not a.no_mbrec)
        print("\n--- acceptance read: exactly how the boot ROM reads the flash ---")
        got = xip_read2(adfu, 0, a.verify, expect=expected_logical(mbrec, brec, lfi))
        ok = got == expected_logical(mbrec, brec, lfi)[:len(got)]
        print("\nVERDICT: " + ("PASS - the ROM will read this; replug the headphones"
                                if ok else "FAIL - do NOT trust this flash; investigate"))
        sys.exit(0 if ok else 1)
    if not a.write:
        print("\ndry run OK: ADFUS, HWSC and FWSC init passed, nothing written.")
        print("Unplug and replug the headphones to leave download mode.")
        return
    write_flash(adfu, brec, lfi, mbrec, with_mbrec=not a.no_mbrec)
    print("Done. Unplug and replug the headphones.")


if __name__ == "__main__":
    main()
