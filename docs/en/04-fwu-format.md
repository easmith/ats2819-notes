# The update file: UPGRADE.HEX, FWU v3 and AFI

How the vendor's update file is built, how the device applies it, and why this is **not** the safe
way to flash. [Русская версия](../ru/04-fwu-format.md). Full working notes:
[../../notes/FWU_FORMAT.md](../../notes/FWU_FORMAT.md).

`UPGRADE.HEX` and `UPGRADE.FWU` are the same file under two names:

```
upgrade.bin (33 536 B, plain MIPS code)  +  encrypted AFI image  +  FwuTail (512 B)
```

## How the device applies it

Reverse-engineered from `upgrade.bin` itself.

1. At power-on the firmware checks its own disk for `UPGRADE.HEX` or `UPGRADE.FWU` in the root
   (`config_upgrade_entry`). If one is there, it starts the `upgrade.ap` application.
2. `upgrade.ap` reads the first 20 bytes — a `UPG` header: the tag, a load address and size, and a
   plain sum of 32-bit words. **There is no signature.** It loads `upgrade.bin` to `0x9fc3a000` and
   runs it.
3. `upgrade.bin` carries its own filesystem driver and its own decryption. It then:
   * opens the file; the encrypted AFI starts at offset `0x8300`, right after `upgrade.bin`;
   * decrypts it block by block (the ATJ2127 cipher, below);
   * loads and runs **HWSC**, identifying the flash chip;
   * looks the chip up in `NOR_ID` for its parameters;
   * loads and initializes **FWSC** — taken from the package, see the warning below;
   * **the first call of the FWSC driver erases the first 64 KB**, the boot-record region;
   * writes, in order: **BREC** → the firmware image (**FWIMAGE**), verified → **MBREC last** →
     resources if any;
   * checks every part against the `checksum_4` in its AFI entry.

MBREC being written **last** is a deliberate safety property: if the update is interrupted there is
no valid boot record, so the device falls into ADFU rather than running half-written firmware. But
it also means you cannot recover from the disk afterwards — that needs working firmware. ADFU is
the way back.

## Why the disk path is not safer than ADFU

> `upgrade.bin` loads `NORFWSC BIN` **from the package itself** (`load_and_run_fwsc`) and runs it at
> `0xbfc2c000`. The write is therefore done by the same SDK loader with the same all-zero seed
> table, so a package built without the seed fix bricks the device exactly as an unpatched ADFU
> write does. The factory firmware's own NOR driver (NOR `0x2f3c` → RAM `0xbfc39200`) takes no part
> in an upgrade.

What the disk path does buy: no service mode, no `sudo`, no button dance. What it costs: **there is
no acceptance check before the write.** The device writes itself at power-on and the only verdict is
whether it boots. For an unproven image, ADFU is preferable — there the seed fix is applied
automatically and `--xip2` can read the result back before you unplug. See
[06-flashing.md](06-flashing.md).

To make a package fit for the disk path, patch its `NORFWSC BIN` entry the same way
(`0x308: 0x34794000 → 0x34790000`), recompute that entry's `checksum32`, and repack the FWU with
`tools/fwu_pack.py`.

## The FWU v3 container

The part after `upgrade.bin` is an FWU version 3 container. The scheme is the one worked out in the
rockbox project (`atjboottool`), and `atjboottool --fwu` decrypts it.

* **Signatures**: `11 22 33 44 55 66 77 88 99 AA BB CC DD EE FF 75`; version `0x7e`; a second
  signature `3F AD F8 B0 2E AF 67 49 …`.
* **The key.** This is the important part: **the private ECC key is inside the file**, in blocks A
  and B, on the sect233k1 curve. The 32-byte session key is therefore fully determined by sector 0
  and blocks A/B — **no device secret is involved**.
* **The data cipher** is "ATJ2127": per-sector (512 bytes), 16 rounds, with the round key derived
  from the session key XORed with constants. Confirmed on the device: `upgrade.bin` contains exactly
  those constants and tables.
* **Layout** — the key blocks are interleaved into the data:

  ```
  sector0 | data[:A] | block A (1024) | data[A:A+B] | block B (512) | remainder
  ```

  where A and B come from bytes `sector0[0x1ee]` and `[0x1fe]` (low 4 bits × 512).

`tools/fwu_pack.py` takes sector 0 and blocks A/B from a reference FWU file — the key travels with
them — and encrypts a new AFI with the same cipher. Verified by decrypting the result back: byte for
byte identical.

## The AFI package and the entries that matter

`ST_AFI_HEAD` is 4096 bytes: the tag `AFI`, VID/PID, date, length, up to 126 directory entries and a
header checksum. Each 32-byte entry has an 8.3 name, type, load address, offset, length, subtype and
checksum. Flashing needs exactly six:

| Name | Type | What it is |
|---|---|---|
| `NORHWSC BIN` | 0x48 | the HWSC scanner, load address `0xbfc2c000` |
| `NORFWSC BIN` | 0x46 | the FWSC write driver, load address `0xbfc2c000` |
| `NOR_ID  BIN` | 0x60 | the SPI-NOR chip table, 128 bytes per chip |
| `NORBREC BIN` | 0x42 | the BREC boot record |
| `FWIMAGE FW ` | 0x49 | the LFI firmware image |
| `NORMBRECBIN` | 0x4D | the MBREC boot record |

After the encrypted AFI comes **FwuTail**, 512 bytes: the marker `0x55aa55aa` and the string
`FwuTail`, a checksum over the whole AFI, and a checksum of the tail itself.

Everything inside the SDK's own `AFI.bin` — including those loaders and the vendor's build scripts —
is stored XOR 0x38 inside a FAT32 volume; `scripts/afi_decode.py` unpacks it, which is how the
loaders and `fwsp.py` became readable in the first place.
