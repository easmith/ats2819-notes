# The device

A bone-conduction headset. It carries the name **B18** on the shell and over Bluetooth, and appears
over USB as a flash disk named **AGP-S22**. What the product is actually called at retail is
unknown — there is no brand on the device, no serial string in the USB descriptor, and the only
names it offers are those two. `AGP-S22` looks like a vendor's model code, but nothing on the
hardware confirms whose. [Русская версия](../ru/01-device.md).

## Identifiers

**Bluetooth.** Advertised name `B18`, class Headset. The lower three bytes of the address are the
LAP, which is a field in the firmware header (`bt_lap`, see
[03-firmware-layout.md](03-firmware-layout.md)) — but on this unit the header holds a *different*
LAP from the address the device actually advertises. The real address comes from elsewhere on the
chip, so the MAC is not literally baked into the firmware image.

**USB (disk mode).** `10d6:1101`, manufacturer `ACTIONS`, product `AGP-S22`, device version
`0x0100`. There is a serial-number descriptor (index 3) but the string is **empty**. The disk is
FAT32, 8 GB, single MBR partition labelled `AGP-S22`, holding the user's own MP3s — no firmware
files.

## Is your unit the same?

Three cheap checks, in order of confidence:

1. **`tts_rc.lib` format.** Pull `config.bin` and `tts_rc.lib` out of a flash dump
   (`tools/extract_all.py`) and look at the first 16 bytes of the prompt library: `MP3 `, sample
   rate, frame size, then the literal `v2  `. That is the US281B-era format.
2. **Config IDs 17–21** (`FACTORY_FW_INFO_*`) read with `tools/cfgdump.py` — these are what
   identified the platform here, by matching the public SDK
   [github.com/Pachouli/2819p](https://github.com/Pachouli/2819p).
3. **The flash chip**, reported by the HWSC loader: `C8 40 15`, a GigaDevice GD25Q16, 2 MB.

If any of these differ, treat every address in this repository as a hypothesis.

## Voice prompts

The firmware carries **31 Chinese prompts and no English ones**. The language cannot be switched by
buttons or by a setting: `tts_rc.lib` has slots for a second language and they are all empty. The
only route to another language is replacing the prompt files themselves —
[08-custom-prompts.md](08-custom-prompts.md).

The prompts are MP3, 16 kHz, 16 kbit/s, mono. All 31 factory clips matched the SDK's own files
byte for byte, which is how their meanings are known exactly.

## ADFU service mode

Actions chips have a boot-ROM service mode called **ADFU**, through which memory can be read and
written without the firmware's help.

* Normal mode (disk): `10d6:1101`.
* Boot ROM: `10d6:10d6`. It answers an inquiry with `\x00CADFUD\x18QA\x00`.
* Entering ADFU: inquiry `0xCC` → reply `ACTIONSUSBD`, then command `0xCB`. Implemented in
  [actions-adfu](https://github.com/sonigeez/actions-adfu) plus
  `patches/actions-adfu-endpoints.patch`, which adds bulk-endpoint autodetection.
* Leaving ADFU: unplug and replug. **But a healthy device also leaves ADFU by itself** after a few
  idle seconds. While the firmware would not boot it sat in ADFU indefinitely, which gave the false
  impression that the mode is stable. To catch a healthy device, a waiter script has to poll for
  `10d6:10d6` and start the instant it appears.
* **ADFU does not charge the battery.** Hours in service mode here flattened it until the protection
  tripped and the device stopped powering on at all; only a long charge brought it back.
* Switching into ADFU from disk mode needs `sudo`: macOS holds the mass-storage driver and it has to
  be detached. Unmount the volume first (`diskutil unmount /Volumes/AGP-S22`).

### Reading the device's state off the USB bus

| What you see | What it means |
|---|---|
| `10d6:1101` plus the `AGP-S22` disk | the firmware is running |
| `10d6:10d6` | the ROM could not start the firmware — there is nothing bootable in flash |
| nothing at all, red LED | the device is off and charging, **or** deeply discharged |

The third row matters: plugging in a healthy headset while it is switched off gives **no USB device**
at all, only charging. That is not a fault. `10d6:10d6`, on the other hand, is a reliable sign that
the firmware is not booting.

### Boot ROM commands used here

* `0xCD 0x93` — read memory;
* `0xCD 0x13` — write RAM;
* `0xCD 0x20` — call code at an address.

One property shapes everything built on top: the ROM's memory read copies **byte by byte**. Ordinary
memory reads back correctly, but 32-bit peripheral registers return rubbish. That is why reading the
flash-mapping registers needed a MIPS stub of our own — see [05-adfu.md](05-adfu.md).

## The tools here

* `tools/adfu_core.py` — ADFU access, and a dumper that reads the mapped flash through the ROM.
* `tools/adfu_stub.py` — runs small MIPS32 programs in the device's RAM (register reads, remapping
  flash, probing the loaders). Writes RAM only, never flash.
* `tools/adfu_flash.py` — writing, and more importantly reading: `--read` (raw physical), `--xip2`
  (through the cache, the way the processor reads), `--regs` (registers via `lw`).
* `tools/extract_all.py` — read-only harvesting of everything the ROM read command can reach.

Anything that touches USB needs `sudo`, e.g. `sudo .venv/bin/python tools/adfu_stub.py regs`.
