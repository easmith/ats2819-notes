<!-- Working notes. Kept as written during the investigation: addresses, disassembly and
     raw measurements, in the order they were found. The readable account is in docs/en/.
     Paths mentioned here are from the private working tree; the published equivalents are
     firmware/stock/nor_logical_1MB.bin, firmware/raw/raw_first_1MB.bin and vendor/afi/. -->

# UPGRADE.FWU / UPGRADE.HEX format (US281B / ATS2819)

Sources: `vendor/2819p/case/fwpkg/build_FWU_HEX.bat`, the sources `case/ap/ap_upgrade/*.c` and `case/ap/ap_config/config_*.c`, `psp_rel/bin/upgrade.bin`, and the vendor's own build script `fwsp.py` (from `psp_rel/bin/AFI.bin`). Every file inside AFI.bin is obfuscated with XOR 0x38; `scripts/afi_decode.py` writes the decoded copies to `vendor/afi/`.

## How it is built (build_FWU_HEX.bat)

```
FW2X.exe -fssf US281B_BTBOX.fw MakeBin AFI UPGRADE.IMG
copy upgrade.bin /b + UPGRADE.IMG /b  UPGRADE.FWU
copy UPGRADE.FWU UPGRADE.HEX            (the same file under two names)
```

```
UPGRADE.FWU = [ upgrade.bin, 0x8300 bytes, plain MIPS code ]
            + [ AFI, encrypted as FWU v3 (if FWU_ENCRYPT=1) ]
            + [ FwuTail, 512 bytes, not encrypted ]
```

## When it runs

`config_poweron.c`: at power-on, if a card is present (`DISK_H` — on this headset that appears to be the internal 8 GB storage) and the root holds `UPGRADE.HEX` or `UPGRADE.FWU` (Unicode or 8.3 name). Without a charging cable it also wants battery level above 1 (card) or above 3 (uhost).

- `UPGRADE.FWU`: factory mode; the device does not reboot after the update.
- `UPGRADE.HEX`: end-user mode. The file is renamed to `UPGRADE~.HEX` and the device reboots after the update.

## Part 1: upgrade.bin (the on-device check, `upgrade_app_main.c`)

```c
struct upgrade_header_t {      // at offset 0 of the file
    u32 checksum;              // sum of u32 words over [4 .. load_size)
    u8  format[4];             // "UPG\0"
    u32 load_size;             // 0x8300
    u32 load_addr;             // 0x9fc3a000
    u32 main_func;             // 0x9fc41c8d (MIPS16)
};
```

`upgrade.ap` checks only `"UPG"` and that sum (verified against the SDK's upgrade.bin: 0xddb10a6e), reads the first `load_size` bytes of the file to `load_addr` and calls `main_func(params)`. There is no signature.

upgrade.bin carries its own FAT/exFAT and uhost drivers, the functions `upgrade_decrypt_read_block`, `upgrade_image_find_file`, `upgrade_load_and_run_hwsc/fwsc`, `upgrade_fw_write`, and the names `NORHWSC BIN`, `NORFWSC BIN`, `NORMBRECBIN`, `NORBREC BIN`, `FWIMAGE FW `, `RESLIB  BIN`. It also holds the FWU v3 constants: the signature `11 22 .. ff 75` at 0x82c0, sig2 `3f ad f8 b0 ..` at 0x82d8, and the decode_A table at 0x7e28 and 0x808.

## Part 2: FWU v3 encryption

The scheme is the one worked out in rockbox's `utils/atj2137/atjboottool/fwu.c`. **In the 2819's upgrade.bin the tables match exactly**: decode_A (1024 bytes, at 0x7e28), sect233k1 b (0x8228), G_y (0x8248), G_x (0x8268), the signature and sig2 (0x82c0, 0x82d8). So it performs the same ECIES procedure.

**The private ECC key is stored in the file itself** (block B, protected by the key in block A), so the 32-byte session key is determined by sector 0 and blocks A/B alone. No device secret is involved.

The data cipher: the US282A UPGRADE.HEX uses the "ATJ2127" variant (atjboottool reports "looks like an ATJ2127 firmware", 16 rounds per 512-byte sector); RC4 is not used. The US282A file has no FwuTail and is a **bare** FWU (signature at offset 0) with no upgrade.bin in front.

The reference file used here was a US282A UPGRADE.HEX plus the key extracted from it (expected at `vendor/fwu/US282A_UPGRADE.HEX` and `.keybuf`; neither is redistributable, which is why `scripts/bootstrap.sh` does not fetch them and `tools/build_fw.py` skips the UPGRADE.HEX step without them). The key is extracted by a copy of rockbox's atjboottool patched to print keybuf (the `KEYBUF_OUT` variable). `tools/fwu_pack.py` takes sector 0 and blocks A/B from that reference FWU and encrypts its own AFI with them. Verified:
- re-encrypting the decrypted US282A reference reproduces the original **byte for byte** (all 1 759 232 bytes);
- a payload of a different size (0x51234 bytes, altered content) packed with `--tail` decrypts in atjboottool with no errors: every check passes, including the FwuTail checksums and the comparison against the input.

Not verified:
- whether the 2819's upgrade.bin accepts sector 0 and blocks A/B taken from a US282A file;
- whether FW2X encrypts the AFI for the 2819 at all (`FWU_ENCRYPT` is not set in fwimage.cfg and the default is unknown);
- where upgrade.bin looks for the start of the FWU. Most likely immediately after itself, at offset 0x8300.

## Part 3: AFI (fwsp.py BuildAFI)

```
ST_AFI_HEAD (4096 bytes):
  "AFI\0", vid u16, pid u16, version[4], date[4] (BCD), length u32,
  sdk_version[4], reserved[8],
  ST_AFI_DIR[126]: name 8.3 [11], type u8, address u32, offset u32, length u32, subtype u32, checksum u32
  reserved[28], head_checksum u32 (= sum of u32 over the first 4092 bytes)
type: 'H'(0x48)=HWSC, 'F'(0x46)=FWSC, 'M'(0x4D)=MBREC, 'B'(0x42)=BREC, 0x60=NAND_ID, 'I'(0x49)=FWIMAGE.FW (LFI), 0x70=RESLIB.BIN
An entry's checksum = sum of u32 over the file's data. The AFI length is padded to 512 bytes.
MBREC: the first 2 bytes = checksum_2(data[2:]) + 0x1234.
```

```
FwuTail (512 bytes, after the encrypted data):
  length=1, type=7, reserved[14], fwu_checksum u32 (sum of u32 over the encrypted AFI),
  flag=0x55aa55aa, "FwuTail\0", crc[32]=0, reserved[444], tail_checksum u32 (sum of the first 508 bytes)
```

## Part 4: FWIMAGE.FW = LFI (fwsp.py BuildLFI)

```
ST_LFI_HEAD (512): lfi_flag u32, sdk_version[4], version[4], vid, pid, dir_checksum u32,
  capinfo[32] (+20), reserved[16], bt_lap[3] (+68), save_bt_flag, reserved[7],
  udisk_setting (+79), usb_setup_info[48] (+80), sdk_description[336], reserved[46],
  head_checksum u16 (= checksum_2 of the first 510 bytes)
ST_LFI_DIR[n] (32 bytes): name[11], attr, subtype, reserved, version u16, offset u32, length u32, reserved[4], checksum u32
  n is rounded up to a cache-line boundary and the last entry stays empty. dir_checksum = sum of u32 over all entries.
File order: drivers (by text_addr, with the entry tables of shared modules merged), then the APs (by text_addr), then everything else.
One more cache line is appended at the end.
```

The LFI is written in the clear. During a write (FWSC, WRITE_LFI, mode 0xC0) it is the FWSC that applies the NOR encoding. When updating, the vendor tool first **reads** the LFI header (WRITE_LFI, mode `0x80000000`, 0x200 bytes) and carries `capinfo`, the BT LAP (+68..70) and usb/sn (+79..127) over into the new header. Disassembling NorFwsc.bin (see RE_NOTES.md) showed that it has no read mode at all: mode 0x80 returns nothing but the stale contents of the RAM buffer, and the very first call of its driver erases NOR 0–0xFFFF. Do not use it on a device.

## What this means for a window-only dump (the old dumps/flash_full.bin)

Module headers (signature `yqhx` at +4): the first complete header sits at 0x62c0 (driver id 0xff). The kernel's own header (id 0x00) is not in the dump. The `0x10000000` window starts **inside the kernel's code**, so the LFI header, the LFI directory, the header and start of kernel.drv, MBREC and BREC are all missing from it. Everything else is there in full (43 headers: drivers, APs, *.al), along with tts_rc.lib and config.bin. The kernel on the device is built differently from the SDK's: chunks of the SDK's kernel.drv appear at a drifting offset.

Card-based upgrading is built into the device's firmware: `config.ap` (header at 0x9ad80) contains the strings `UPGRADE HEX`/`UPGRADE FWU` (at 0x9bc0c), and `upgrade.ap` (header at 0x9f740) contains `upgrade_file_open` and `UPGRADE~HEX`.

Conclusion at that point: the format was understood and the encryption reproducible, but building FWIMAGE.FW still lacked this device's LFI header, its directory and the start of its kernel. The options were our own code via ADFU CALL_ENTRY (read NOR, repoint the XIP window) or assembling the LFI entirely from the SDK. The second is riskier: the result would not be the firmware the device actually runs. Reading through NorFwsc is not possible, see RE_NOTES.md.

## upgrade.bin, what it actually does (disassembly: build/eats2819/dis/upgrade16.txt)

upgrade_main 0x9fc41c8d(params):

1. **file_open** 0x9fc41351 opens `UPGRADE.FWU` (factory) or `UPGRADE~.HEX` (end user) on DISK_H/DISK_U. It reads the 20-byte UPG header. FWU offset = header.load_size (0x8300). File size ≤ load_size → SIZE_ERR.
2. **load_and_run_hwsc** 0x9fc4179d:
   - AFI file `NORHWSC BIN` → loaded to dir.address, checksum32 must equal dir.checksum (CHECKSUM_ERR);
   - call; it must return a ptr to {buf, len=36} (RET_ERR);
   - flash id = status+20.
3. **find_flash_info** 0x9fc418b5: `NOR_ID  BIN` (type 0x60), 128-byte records. Match the 4 id bytes with 0xFF wildcard → ctx+24, else UNKNOWN.
4. **load_and_run_fwsc** 0x9fc419c5:
   - `NORFWSC BIN` → dir.address (0xbfc2c000), checksum checked;
   - copies the matched NOR_ID record to hdr+112;
   - hdr+388 = 0 (factory) / 3 (end user);
   - calls +0x200, which must return len 36; the driver is +0x400.
5. drv(0,0,0,0): the first NorFwsc driver call erases NOR 0–0xFFFF.
6. fw_write(`NORBREC BIN`, 0x46), fw_write(`FWIMAGE FW `, 0xC0, then drv(0xFF) must be 0, else VERIRY_ERR), fw_write(`NORMBRECBIN`, 0x40), optionally fw_write(`RESLIB  BIN`, 0xC1). Then drv(1), progress 100.

**MBREC is written last**: an interrupted upgrade leaves no valid MBREC, so the ROM should fall back to ADFU.

fw_write: 32 KB chunks → drv(mode, pos, n, buf). checksum32 of the data must equal dir.checksum (checked *after* writing). image_find_file looks only at the **first 15** AFI dir entries (first 512 bytes of the AFI head). The AFI head checksum is not checked.

FWU read (decrypt_read_block 0x9fc4152d):
- block 0 = 18 432 bytes at fwu_off (sector0 + blocks A/B + first 16 KB) → key setup 0x9fc3b9e8;
- block n = 16 KB at fwu_off + n + 2048, decrypted by **ATJ2127** sector cipher 0x9fc3bc28 (constants 0x42146ea2… at 0x9fc3bedc; rounds and key at state+260/+264).

So the payload layout is exactly what tools/fwu_pack.py produces. Constraint: offA + offB ≤ 16 KB (true for any A,B ≤ 15).

## NorFwsc details that matter for the AFI

- Write path 0xbfc2fa95: logical→physical = addr/64·66 (every 64-byte line is stored with a 2-byte CRC16, 0xbfc2f6a1). The "randomizer" bits 0x2000|0x8000 in SPI0 CTL are set while writing whenever flag 0xbfc2fc60 bit 7 is set. 0xbfc2e9e5 always sets that flag. No key is needed from the host.
- Init chip-id check: writes hdr chipid table (hdr+240, index hdr+380) to SPI0+0x14 and reads it back, status 4 on mismatch. The SDK packages define no CHIPID/PACKAGEID, so the table is 0. The device reads SPI0+0x14 = 0.
- MBREC write (0x40): recomputes [0..1] = checksum_2 + 0x1234 and the size.
- BREC write (0x46) copies 64 bytes of flash-chip info into BREC+64.

## Device boot records (dumps/nor_logical_1MB.bin)

- MBREC = NOR[0:0x400] (byte 2 = 0x5A). Built from a different SDK than ours: 624 bytes differ.
- BREC = NOR[0xfc0:0x4280], 0x32c0 bytes. Header:
  - checksums ebec/eedf (both verify), len/all 0x32c0, welcome_off 0x2dc0;
  - **lfi_cap 0xddfc0** (+20), spi_stg_cap 0xF0000 (+24), **decrypt_mode 0** (+28);
  - boot_config (+128): 000000000400000001000000010000000100000001000000000000000000000000000000080000000000000000f0010000100000000000000000000000000000;
  - bonding 03010f00 (+192), mode_para 0.
- The device NOR is **not** encrypted (decrypt_mode 0), so the FWSC header must have +16 = 0 and +20/+24 = 0.

## Plan for our AFI

- NORMBREC = device MBREC; NORBREC = device BREC with lfi_cap and both checksums updated; FWIMAGE = device LFI with the new TTS_RC.LIB (files after it shifted, 64-byte aligned, dir/head checksums recomputed). The new LFI ends at ≈0xEDB70, below VRAM 0xF0000.
- NORHWSC / NORFWSC / NOR_ID from SDK AFI.bin. FWSC header:
  - +2 = 0, +8 = lfi_cap, +12 = spi cap, +16 = 0, +28 = 1;
  - +29 = bonding;
  - +48 = FF×16;
  - +240 = zeros;
  - +384 = boot_config from the device BREC.
- Before writing anything: dry-run HWSC + FWSC init on the device through an ADFU stub (no driver call, so no erase) and check status 0 and flash-id match.

## Built and verified on the host (2026-10-25): build/fw/UPGRADE.HEX

tools/build_fw.py produced build/fw/UPGRADE.HEX (1 054 976 B = upgrade.bin 0x8300 + FWU). Verified entirely on the host, nothing sent to the device:
- atjboottool --fwu decrypts it: signature/size/version/tail all Ok, ECIES Pass, ATJ2127.
- decrypted AFI == build/fw/afi_plain.bin (byte-identical), AFI head checksum ok.
- 6 AFI entries, every checksum32 ok: NORHWSC(0xbfc30000), NORFWSC(0xbfc2c000), NOR_ID, NORBREC, FWIMAGE(len 0xe9900), NORMBREC.
- FWIMAGE vs device LFI: only TTS_RC.LIB changed (39 872 → 87 296 B, == build/tts/tts_rc.lib padded to 64); all other 46 files byte-identical; dir/head checksums recomputed.
- BREC: lfi_cap 0xe9900, decrypt_mode 0, both checksums ok; only bytes 0-3 (checksums) and 20-22 (lfi_cap) differ from device.
- MBREC byte-identical to device.
- FWSC: only header bytes 9-433 changed (operate=0, lfi_cap, spi_cap 0xF0000, decrypt=0, upgrade=1, bonding 03010f00, chipid=0); loader code from +0x200 intact.
- Chip GD25Q16 (device BREC+64 == SDK nor_id record at 0x2280).

Still unverified on the device: that SDK HWSC/FWSC run on this unit and the chip-id check passes with 0. Next step (no flash write): ADFU stub dry-run of HWSC + FWSC init only.
