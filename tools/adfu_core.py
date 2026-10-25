#!/usr/bin/env python3
"""Talk to the Actions boot ROM over ADFU, and read memory through it.

This is the module every other tool opens the device with, and it is also a usable dumper on its
own: the SPI NOR is memory-mapped at 0x10000000, and the boot ROM's CBW 0xCD/0x93 reads arbitrary
memory. That read cannot modify flash, which is why it is the only dumping path used here.

Never use the ADFU 0x10 "flash" command as a read. Per docs/en/05-adfu.md the FWSC driver has no
read mode: modes 0x00/0x40/0xC0 all run write routines, and the AFI NorFwsc erases the boot records
on its first driver call in any mode.

Flow:
  1. If the device is in USB-storage mode (10d6:1101), ask it to switch to download mode
     (inquiry 0xCC -> "ACTIONSUSBD", then 0xCB) and wait for the boot ROM (10d6:10d6).
  2. In download mode, auto-detect the bulk endpoints the device reports (never hardcode them).
  3. Read memory at 0x10000000 in 4 KB chunks.

Usage (USB access needs root on macOS):
  sudo python3 tools/adfu_core.py --probe
  sudo python3 tools/adfu_core.py --out build/dumps/flash_full.bin --size 0x400000
"""
import argparse
import importlib
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from paths import BOOTSTRAP_HINT, BootstrapMissing, VENDOR_ROOT, out_dir  # noqa: E402

FLASH_BASE = 0x10000000      # SPI NOR is memory-mapped here
CHUNK = 4096                 # 32 KB transfers stalled on this Mac; 4 KB is proven


def _import_or_hint(name):
    """Import a bootstrapped dependency, or say how to get it.

    Imports are deferred to call time so that every tool in this directory can be imported, read
    and tested on a bare Python — only actually talking to a device needs pyusb and actions-adfu.
    """
    src = VENDOR_ROOT / "actions-adfu" / "src"
    if src.is_dir() and str(src) not in sys.path:
        sys.path.insert(0, str(src))
    try:
        return importlib.import_module(name)
    except ImportError as exc:
        raise BootstrapMissing(f"{name} is not installed — {BOOTSTRAP_HINT}") from exc


def _usb():
    usb = _import_or_hint("usb")
    _import_or_hint("usb.core")
    _import_or_hint("usb.util")
    return usb


class _Lazy:
    """Stands in for a module or class that only exists once the dependencies are installed."""

    def __init__(self, resolve, name):
        self._resolve, self._name = resolve, name

    def __getattr__(self, attr):
        return getattr(self._resolve(), attr)

    def __repr__(self):
        return f"<lazy {self._name}>"


P = _Lazy(lambda: _import_or_hint("actions_adfu.protocol"), "actions_adfu.protocol")
UsbLink = _Lazy(lambda: _import_or_hint("actions_adfu.cli").UsbLink, "actions_adfu.cli.UsbLink")


def wait_for_rom(timeout=15):
    return _import_or_hint("actions_adfu.cli").wait_for_rom(timeout)


def find_storage():
    usb = _usb()
    try:
        devices = list(usb.core.find(find_all=True, idVendor=P.VID))
    except usb.core.NoBackendError as exc:
        raise RuntimeError(
            "pyusb found no USB backend: libusb is not installed. On macOS: brew install libusb; "
            "on Debian/Ubuntu: apt install libusb-1.0-0."
        ) from exc
    for d in devices:
        if d.idProduct != P.PID_ROM:
            return d
    return None


def switch_to_download():
    """Storage mode -> ADFU download mode (same exchange as `actions-adfu switch`)."""
    usb = _usb()
    s = find_storage()
    if s is None:
        return None
    try:
        if s.is_kernel_driver_active(0):
            s.detach_kernel_driver(0)
    except Exception:
        pass
    usb.util.claim_interface(s, 0)
    link = UsbLink.autodetect(s)
    rom = P.RomSession(link)
    ident, status = rom.inquiry()
    print("inquiry:", ident, "status", status)
    if ident != b"ACTIONSUSBD":
        raise RuntimeError(f"unexpected identify reply {ident!r}; not touching anything")
    link.write(P.cbw(rom._next(), 2, True, [0xCB]))
    link.read(2)
    P.parse_csw(link.read(13))
    usb.util.dispose_resources(s)
    print("switch requested; waiting for download mode…")
    return wait_for_rom(15)


def open_adfu():
    """The device in ADFU download mode, switching it there from storage mode if needed."""
    usb = _usb()
    try:
        d = usb.core.find(idVendor=P.VID, idProduct=P.PID_ROM)
    except usb.core.NoBackendError as exc:
        raise RuntimeError(
            "pyusb found no USB backend: libusb is not installed. On macOS: brew install libusb; "
            "on Debian/Ubuntu: apt install libusb-1.0-0."
        ) from exc
    if d is None:
        d = switch_to_download()
    if d is None:
        raise RuntimeError(
            "No device in ADFU download mode (10d6:10d6). Plug the headset in; if it mounts as a "
            "disk instead, this tool switches it over, but USB access needs root — rerun with sudo."
        )
    d.set_configuration()
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(out_dir("dumps") / "flash_full.bin"))
    ap.add_argument("--size", type=lambda s: int(s, 0), default=0x400000)
    ap.add_argument("--probe", action="store_true", help="read the first 4 KB only")
    a = ap.parse_args()

    dev = open_adfu()
    link = UsbLink.autodetect(dev)
    rom = P.RomSession(link)

    head = rom.read_ram(FLASH_BASE, CHUNK)
    known = [b"load ap:", b"fat index", b"USBC"]
    # sanity: the first dwords should be pointers into 0x1000xxxx
    ptrs = [int.from_bytes(head[i:i + 4], "little") for i in range(0, 32, 4)]
    plausible = all(0x10000000 <= p < 0x10400000 for p in ptrs if p)
    print("sector0 head:", head[:32].hex(" "))
    print("first dwords plausible flash pointers:", plausible)
    print("markers in first 4 KB:", {k.decode(): head.find(k) for k in known})

    if not plausible and not any(k in head for k in known):
        sys.exit("read returned unrecognised data; refusing to continue.")
    print("read path OK")
    if a.probe:
        return

    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    written = 0
    t0 = time.time()
    with open(a.out, "wb") as f:
        addr = 0
        while addr < a.size:
            n = min(CHUNK, a.size - addr)
            try:
                data = rom.read_ram(FLASH_BASE + addr, n)
            except Exception as e:
                print("\nread failed at 0x%08x: %s" % (addr, e))
                print("partial dump kept in", a.out)
                break
            f.write(data)
            written += len(data)
            addr += n
            print("\r0x%08x / 0x%08x" % (addr, a.size), end="", flush=True)
        print()
    print("wrote %d bytes to %s in %.1fs" % (written, a.out, time.time() - t0))


if __name__ == "__main__":
    main()
