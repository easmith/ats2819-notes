# The firmware: what is in flash and how it is built

[Русская версия](../ru/03-firmware-layout.md).

Abbreviations: **NOR** is the SPI flash; **XIP** execution straight out of it; **LFI** the firmware
image (a directory of files); **AFI** the packaging used for flashing; **FWU** the encrypted
transport container, i.e. the update file.

> Before you write anything: "encryption = 0" in this document refers to *transport* encryption and
> to the `decrypt_mode` field in BREC. It does **not** mean the data sits in flash in the clear. The
> SPI controller's hardware randomizer is always on and its seed is the chip's CHIPID. See
> [02-flash-format.md](02-flash-format.md).

## What is in flash

The logical image (first 1 MB) is three parts plus a settings region:

```
0x00000  MBREC      boot record (sector 0)
0x00fc0  BREC       secondary boot record + parameter header + logo
0x04280  LFI        the firmware image: header + directory + files
         ...        (kernel, drivers, applications, audio libraries, tts_rc.lib, config.bin)
0xe2240  end of LFI on the factory image; FF beyond it — free space
0xf0000  VRAM       saved settings, volume, pairings and so on
```

Two kinds of checksum are used throughout: **checksum_2**, the sum of 16-bit words (MBREC, BREC, the
LFI header) and **checksum_4**, the sum of 32-bit words (directory entries, file data, and inside
AFI).

### MBREC (`0x0000`, 1024 bytes)

Sector 0. Byte 2 is `0x5A`, the marker for a 1024-byte record (`0xA5` would mean 512). The first two
bytes hold `checksum_2(data) + 0x1234`. The boot ROM reads MBREC first thing at power-on.

### BREC (`0x0fc0` … `0x4280`)

A `0x80`-byte header of device parameters:

* `+0..1` overall checksum = `checksum_2 + 0x1234`; `+2..3` header checksum;
* `+8` total size; `+20` **lfi_cap**, how many bytes the firmware image occupies;
* `+24` system-disk capacity (`0xF0000`); `+28` **decrypt_mode** (`0` on this device) — a different
  mechanism, and **not** a switch for the hardware randomizer;
* `+128` boot_config (64 bytes of boot parameters); `+192` bonding (die parameters);
* then welcome-screen code and resources, irrelevant on a headset.

### LFI (`0x4280` …) — the firmware itself

* **Header** (512 bytes): flag `0x0ff0aa55`, SDK version (`2.30`), firmware version (`1.00`),
  VID/PID, `capinfo` (disk capacities), **bt_lap** at `+68`, **usb_setup** at `+80` (the USB
  string), a build description, `dir_checksum` over the directory, `head_checksum` at `+510`.
* **Directory**: 32-byte entries (`ST_LFI_DIR`) with an 8.3 name, attribute, offset, length and the
  file's checksum. The last entry is empty. The factory image holds **47 files**.
* **Files**, each aligned to 64 bytes: the kernel `kernel.drv`, drivers (`*.drv`), applications
  (`*.ap` — Bluetooth, player, radio, upgrade and so on), audio libraries (`*.al`),
  **`tts_rc.lib`** (the voice prompts) and `config.bin`.

`lfi_cap` is the aligned end of the last file plus one cache line (`0x40`). On the factory image the
LFI ends at `0xe2240`.

## How the vendor builds it

The build tool is `Maker.exe` plus a set of Python scripts (`fwsp.py`, `Production.py`, …). In the
shipped package those scripts are obfuscated with a plain XOR 0x38, and decoding them is the main
source of knowledge about these formats — `scripts/afi_decode.py` does that.

`fwsp.py` in order:

1. **BuildLFI** — assembles the image from individual files: drivers first (merging entry tables for
   modules that share a load address), then applications, then everything else; computes the
   directory and header checksums.
2. **BuildAFI** — wraps it all in an AFI package and appends a FwuTail.
3. If `FWU_ENCRYPT` is on, encrypts the AFI.

The vendor's batch files reduce to:

```
Maker.exe -c fwimage.cfg -o US281B_BTBOX.fw        # build the firmware
FW2X.exe -fssf US281B_BTBOX.fw MakeBin AFI UPGRADE.IMG
copy upgrade.bin /b + UPGRADE.IMG /b  UPGRADE.FWU  # prepend the upgrade loader
copy UPGRADE.FWU UPGRADE.HEX                       # the same file under two names
```

So an update file is `upgrade.bin` (plain code) + an encrypted AFI + FwuTail.

### The AFI package

`ST_AFI_HEAD` (4096 bytes): the tag `AFI`, VID/PID, date, length, up to 126 directory entries and a
header checksum. Each `ST_AFI_DIR` entry is 32 bytes: 8.3 name, **type**, load address, offset,
length, subtype, checksum. The entries needed to flash a device:

| Name | Type | What it is |
|---|---|---|
| `NORHWSC BIN` | 0x48 | the HWSC scanner (load address `0xbfc2c000`) |
| `NORFWSC BIN` | 0x46 | the FWSC write driver (load address `0xbfc2c000`) |
| `NOR_ID  BIN` | 0x60 | the SPI-NOR chip table (128 bytes per chip) |
| `NORBREC BIN` | 0x42 | the BREC boot record |
| `FWIMAGE FW ` | 0x49 | the LFI firmware image |
| `NORMBRECBIN` | 0x4D | the MBREC boot record |

## Pulling the firmware out, read-only

The hard part: getting the flash content without writing anything.

**Reading the XIP window.** The boot ROM can read memory (`0xCD 0x93`) and the flash is mapped at
`0x10000000`, so the first dump is a plain read of that window (`tools/adfu_core.py`). But the window
does not start at the beginning of flash — the mapping puts it at logical offset `0x5980`, so MBREC,
BREC, the LFI header and directory and the start of the kernel are **not** in that dump.

**Byte-wise reads.** The ROM's read command copies memory byte by byte. Ordinary memory is fine, but
32-bit peripheral registers return rubbish, so the mapping registers could not be read directly.

**A stub of our own.** `tools/adfu_stub.py` uploads a tiny MIPS32 program into the device's RAM and
runs it through the ROM. It performs real 32-bit loads and stores and returns into the ROM's command
loop (`0xbfc040ed`), leaving the device in service mode.

* `adfu_stub.py regs` read all 16 mapping entries and the SPI0 registers. That is how the layout
  became clear: entry 1 maps virtual `0x10000000` to logical `0x5980`; entries 5 and 9–12 are free;
  bit 30 of a mapping word means "active".
* `adfu_stub.py map0` claims a mapping entry — the way the ROM does — pointing virtual `0x02000000`
  at logical offset 0, and reads the first 64 KB. The overlap at `0x5980` matched the earlier dump,
  which is the cross-check.

**The full image.** `firmware/stock/nor_logical_1MB.bin` is the start from the `map0` read plus the
rest from the window read: a complete **decoded logical image** of the first megabyte. Every
checksum — header, directory and all 47 files — verifies. It is not a raw dump of the chip; it is
the image as the controller sees it. The raw counterpart is `firmware/raw/raw_first_1MB.bin`, and
`tools/verify_raw.py` checks one against the other line by line.

## Building an image with new prompts

`tools/build_fw.py` builds an update from the **device's own firmware**, replacing only the voice
prompts:

1. Takes MBREC, BREC and the LFI image out of `firmware/stock/nor_logical_1MB.bin`.
2. Replaces `TTS_RC.LIB` in the LFI with the new one, shifts every file after it, updates their
   offsets in the directory and recomputes the file, directory and header checksums.
3. Updates `lfi_cap` and both checksums in BREC.
4. Takes HWSC, FWSC and `nor_id.bin` from the SDK (via `scripts/bootstrap.sh`) and fills the FWSC
   header with this device's parameters: operate = 0, `lfi_cap`, capacity, encryption = 0, the
   upgrade flag, bonding, boot_config, and a chipid table of zeros.

   > That chipid table at `+240` is the **randomizer seed table**, and zeros are exactly what it must
   > not be when writing: the loader's init latches a seed from it and sets `CTL` bit 14. Writing
   > over ADFU therefore needs `use_hw_seed()` from `tools/adfu_flash.py`, which leaves bit 14 clear
   > so the write uses the chip's built-in seed.
5. Assembles the AFI (6 entries), encrypts it (`tools/fwu_pack.py`) and prepends `upgrade.bin`.

Outputs land in `build/fw/`: `afi_plain.bin` (what ADFU writes) and `UPGRADE.HEX` (the disk-upgrade
file). The published `firmware/prompts-ru-en/afi_plain.bin` is the image that was actually flashed.

### Host-side verification, no device involved

* `atjboottool --fwu` decrypts the file: signatures, size, version and tail all check out.
* The decrypted AFI matches what was assembled; the header checksum and all 6 entry checksums verify.
* Inside the firmware image **only** `tts_rc.lib` changed; the other 46 files are untouched.
* BREC, MBREC and the FWSC header are filled in correctly.

### Loader verification on the device, without writing flash

`tools/adfu_stub.py hwsc` and `… fwsc` load HWSC and FWSC at `0xbfc2c000` and call **init only** —
the write driver is never called, so nothing is erased:

* HWSC returned `C8 40 15` (GD25Q16);
* FWSC init returned status `0` — the chip-id check passed.

So that the device survives the loaders reconfiguring SPI, the stub **restores** the SPI0 and mapping
registers from the values it read earlier and returns into the ROM, leaving the XIP window working
and the headset in service mode.

## Voice prompts (`tts_rc.lib`)

* Format "v2": a 16-byte header (`MP3 `, sample rate, frame size, the tag `v2`), then 8 bytes per
  slot (offset + length), two slots per prompt (language 0 and language 1), then the MP3 data.
* Audio: MP3, 16 kHz, 16 kbit/s, mono, constant bitrate (72-byte frames).
* The factory library is 39 872 bytes: 31 Chinese prompts, second-language slots empty.
* The replacement (Russian + English) is built by `tools/make_tts.py`.

**The size limit.** Only 56 768 bytes are free before the VRAM region at `0xF0000`. The new
`tts_rc.lib` is 87 280 bytes — 47 408 more than the factory one — and fits with about 9 KB to spare.
That is why the phrases are short: a full bilingual set at natural length came to roughly 112 KB and
did not fit.
