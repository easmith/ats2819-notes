<!-- Working notes. Kept as written during the investigation: addresses, disassembly and
     raw measurements, in the order they were found. The readable account is in docs/en/.
     Paths mentioned here are from the private working tree; the published equivalents are
     firmware/stock/nor_logical_1MB.bin, firmware/raw/raw_first_1MB.bin and vendor/afi/. -->

# ATS2819 (US281B) ADFU loaders: static notes

Tools: GNU binutils objdump (`brew install binutils`), `-b binary -m mips:16|mips:isa32r2 -EL --adjust-vma=...`. Disassembly is in `dis/`.

## Load addresses
- ADFUS.BIN loads at 0xbfc28000. The ebase write and the literal pool (0xbfc28bxx globals) match, and the entry jalr target 0xbfc2836d (MIPS16) falls inside the image.
- Fwsc.bin loads at **0xbfc2c000**, not 0xbfc2c200. The entry is at +0x200 = **0xbfc2c200** (MIPS32), and its 512-byte header sits at 0xbfc2c000; the entry code computes `lui 0xbfc3; addiu -0x4000`. With base 0xbfc2c000, the jalx targets land on MIPS16 `save` prologues.
- Fwsc.bin +0x400 = **0xbfc2c400** is the flash driver `f(mode, addr, len, buf)`. ADFUS pre-sets its driver pointer (0xbfc28bbc) to this address.
- HWSC: its literal pool references 0xbfc30000 and 0xbfc303a0, which fits loading at 0xbfc30000. I did not verify this in detail.

## ADFUS dispatch
- CBW cb[0] = 0xCD selects vendor commands; 0xB0 selects reset (it sets the flag to 'w').
- cb[1] & 0x7F is the sub-command, and cb[1] & 0x80 marks the IN (read) direction:
  - 0x10 RW_PHY: 0xbfc28661
  - 0x13 RAM (0x93 reads): 0xbfc284a5
  - 0x20 SWITCH: 0xbfc28511
  - 0x21 CALL_ENTRY: 0xbfc28561. It calls the given address with no arguments and stores the return value.
  - 0x22 POLL: sends *(ret)+4.
  - 0x23 GET_STATUS: sends len bytes from *(ret).
  - 0x60 DOWNLOAD_IMAGE: 0xbfc28775.
- 0x10 handler: `mode = addr >> 24`, `addr &= 0xFFFFFF` (in bytes), data is moved in chunks of up to 8 KB through 0x9fc20000.
  - mode 0xFF: calls drv(0xFF,0,0,0) and receives OUT data.
  - mode & 0x40: receives OUT data, then calls drv(...). This is the write path.
  - Any other mode: calls drv(...), then **sends** the buffer IN. This is the only read-shaped path.

## FWSC driver (0xbfc2c400) mode decode
| mode | action |
|---|---|
| 0xFF | nothing (returns 0) |
| bit 0x80 without 0x40 (e.g. **0x80**) | nothing. ADFUS would just send back whatever is in the RAM buffer |
| 0xC0 (low nibble 0) | LFI write; the first call also prepares the region |
| low nibble 0 (0x00 / 0x40) | **MBREC write**: sector 0, signature 0x1234 |
| low nibble 6 (0x06 / 0x46) | **BREC write** at +1024 |

**There is no read mode.** Modes 0x00 and 0x06 run the **write** routines even when issued in the IN direction, so they must never be sent: they would overwrite the boot records with garbage.

## Conclusion
- The vendor ADFUS + FWSC pair offers no read-only NOR dump command.
- Safe steps: ADFUS upload + SWITCH, HWSC call + GET_STATUS (identify only, inferred), and FWSC init call + GET_STATUS. FWSC init does no writes on the path I traced.
- Any 0x10 command except 0x80 / 0xFF can modify flash.
- Dumping would need a custom routine run via CALL_ENTRY (output collected with 0x93 RAM reads). Not analysed further.

## NorFwsc.bin (from psp_rel/bin/AFI.bin, XOR 0x38 removed): afi_base/NorFwsc.bin, 15280 bytes

This is the FWSC the production/upgrade flow (fwsp.py) uses. It is **not** the Fwsc.bin above. Disassembly: `dis/norfwsc32.txt`, `dis/norfwsc16.txt`.

- Load address 0xbfc2c000, same as Fwsc.bin. Init entry +0x200 (MIPS32): it computes base = `lui 0xbfc3; addiu -0x4000`, and BSS starts exactly at the end of the file (0xbfc2fbb0). Driver `f(mode, addr, len, buf)` at +0x400 (MIPS32).
- The 512-byte header is filled in by the host before upload (fwsp.py FWSC_Data):
  - +2 operate (FLASH_ERASE)
  - +3 bt flag
  - +8 lfi_cap
  - +12 spi cap
  - +16 spi_decrypt (1 = efuse, 2 = test key)
  - +20/+24 efuse key
  - +28 upgrade flag
  - +29 bonding
  - +48 SAVE_VM[16]
  - +112 NOR_CHIPINFO
  - +240 chipid
  - +380 chipid index
  - +384 boot config
  - +448/+452 spi1
- **Init (+0x200)**: SPI init from the chip info at +112, chip-id check, then a 36-byte status (status=1 means no flash, 4 means chipid mismatch, capacity at +20). **If header[2] == 2 it erases the whole chip** (0xbfc2df35(0, capacity<<9)). It does not read the LFI.
- **Driver (+0x400)**: on the **first call in any mode** (global 0xbfc2fbec == 0) it runs 0xbfc2dc8d. That routine calls **0xbfc2df35(0, 0x10000) = ERASE NOR 0x0000–0xFFFF** (MBREC/BREC). df35 walks the range in 64 KB blocks. df15 → 0xbfc2d2c5 sends WREN 0x06, then the chip's erase opcode and a 24-bit address.

| mode | handler |
|---|---|
| 0x00 | nothing |
| 0x01 | 0xbfc2dcf9 |
| 0x40 | MBREC write 0xbfc2f4e5 |
| 0x46 | BREC write 0xbfc2f529 |
| 0x80 and any other value | nothing |
| 0xC0 | LFI write 0xbfc2f605 |
| 0xC1 | RESLIB 0xbfc2f629 |
| 0xC2 | ext lib 0xbfc2dc25 |
| 0xFF | finish 0xbfc2ddb5 |

- There is **no read mode**. The ADFUS 0x10 handler is identical in both ADFUS versions: when bit 0x40 is clear it calls the driver and then sends the RAM buffer 0x9fc20000. So fwsp.py's "READ_LFI_HEAD" (mode 0x80) just returns stale buffer contents; its checksum test then decides that the head is not valid.
- Production order (Production.py): ADFUS, HWSC, FWSC init, BREC (0x46, first driver call, which triggers the 64 KB erase), LFI (0x80 read attempt, then 0xC0), MBREC (0x40), OVER (0xFF).
- **Never upload NorFwsc.bin and send any 0x10 command**: even 0x80 or 0xFF erases the boot block. Fwsc.bin (above) has no such first-call hook. Its 0xFF returns immediately.
- A raw NOR read primitive exists at 0xbfc2d68d(ctx, addr, buf, len). It picks opcode 0x6B (ctx[4]==4), 0x3B (==2) or 0x0B and calls the SPI transfer 0xbfc2d5ed. A custom CALL_ENTRY stub could use it, but it needs ctx set up and would most likely return encrypted bytes. Not analysed further.

## Device read 2026-10-25 (dumps/extract/, read-only via ROM 0x93)

- XIP 0x10000000, 1 MB: byte-identical to dumps/flash_full.bin.
- 0xbfc00000–0xbfc08000 is the boot ROM ("UCOS @Actions", ADFU). RAM holds data at 0xbfc1c000–0xbfc48000 and is zero above that. The RAM is the running firmware's memory at the moment ADFU was entered: FAT dir cache of the user disk, BT address stored reversed (redacted: it is this unit's own MAC), "AGP-S22 2.00". There is **no** LFI dir and no LFI head in RAM.
- Registers:
  - SPI0: CTL=0x38, STATUS=0x96, BC=1, +0x18=0x40, +0x20=0x80.
  - MMU entries 0 and 1: mapping 0/0 and phy 0/0x80.
  - c012001c = 0x1c.
- MMU (kernel hal_mmu_read/set_cache_entry in the XIP dump at 0x100066f4/0x10006714): entry i is the pair `mapping = c00a0090+8i`, `phy = c00a0094+8i`, 16 entries.
- The boot ROM (0xbfc014b5) invalidates all 16 entries (writes 1 to each mapping word), then sets entry 0: mapping=0x42000000, phy=arg (NOR offset; the boot loop starts at 0). It then reads MBREC at **virtual 0x02000000** (0xbfc00fd9; byte 2: 0xA5 means 512 B, 0x5A means 1024 B). So the mapping word is most likely bit30 = valid plus the virtual base. BREC uses mapping 0x41000000 (virtual 0x01000000).
- c00a0110/0114 are the cache miss/hit counters (kernel 0x10004870).
- **The ROM RAM command is byte-wise.** 0x13/0x93 → 0xbfc03c31 uses `lbu`/`sb`. Memory dumps through it are fine. **32-bit peripheral registers read through it are garbage** (only the low byte is non-zero, and c012001c reads 0x1c), and writing registers through it is unsafe. Hence tools/adfu_stub.py: a MIPS32 stub at 0xbfc28000 does `lw` reads into 0xbfc29000 and then `jr 0xbfc040ed` back into the ROM ADFU loop (which re-inits USB and clears the run flag at state+5, 0xbfc0324d). The ROM run command (0x20, 0xbfc03e19) only sets state+5=0xff. The loop at 0xbfc040ed then calls 0xbfc000c8 `jr a0`, which never returns. ROM sp = 0xbfc45ff0.

## Full logical NOR image recovered (2026-10-25)

Registers read with `lw` (tools/adfu_stub.py regs, dumps/extract2/registers_lw.txt): **MMU mapping word = 0x40000000 (valid) | virtual base; phy = NOR byte offset**. The live entries include 1: 0x10000000 → NOR 0x5980 (this is the window that dumps/flash_full.bin came from), 2: 0x11000000 → 0x17600, 8: 0x30000000 → 0xd9a00. Entries 5 and 9–12 are free.

`adfu_stub.py map0` set entry 9 = (0x42000000, phy 0), the same thing the ROM does at boot, and read virtual 0x02000000 → dumps/extract2/nor_0x000000.bin (64 KB, decrypted). Bytes 0x5980.. equal flash_full.bin.

**dumps/nor_logical_1MB.bin** = nor_0x000000[:0x5980] + flash_full[:0x100000-0x5980]. This is the complete *decrypted* logical NOR, sha1 742cf9ab86976cdc7c7c139a2f258a4f83780f41. It is not a raw/encrypted image.

| NOR range | contents |
|---|---|
| 0x0000–0x0400 | MBREC (byte 2 = 0x5A, 1024 B) |
| 0x0400–0x0fc0 | FF |
| 0x0fc0–0x4280 | BREC area |
| 0x4280 | LFI head |
| 0x4480 | LFI dir, 47 entries |
| 0x4a80 | kernel.drv |
| to 0xe2200 | LFI files; +0x40 tail → 0xe2240 |
| 0xe2240–0xF0000 | FF, 56 768 B free |
| 0xF0000–0x100000 | VRAM (VM_Capacity 0x10000). Reads all FF through the window, so VRAM is either really empty or not readable this way |

LFI head:
- lfi_flag 0x0ff0aa55, SDK "2.30", VER "1.00", vid/pid 0
- capinfo: SD_Capacity 0xF0000, VM 0x10000
- bt_lap ad3d72 (≠ device MAC 46:5F:C5, so the MAC is stored elsewhere)
- udisk_setting 1, usb_setup "4512482ADF0FEEEE", sdk_description "20150307_SVN0025_FPGA"

Head checksum, dir checksum and all 47 file checksums verify.

TTS_RC.LIB: dir #40 at LFI+0xc94c0 (NOR 0xcd740), 39 872 B, header "MP3 " 16000 Hz, frame 72 (16 kbps) → dumps/tts_rc_device.lib. The new ru+en build/tts/tts_rc.lib is 111 832 B (+71 960) and **does not fit** in the 56 768 B before VRAM. The prompts must be shrunk to ≤ ~96 KB.
- 2026-10-25: shorter phrases approved by the user (tools/make_tts.py, pad 0.05 s). build/tts/tts_rc.lib is now 87 280 B (+47 408 vs stock), which fits the 56 768 B free with 9 360 B to spare. All 31 stock clips are byte-identical to the SDK tts_res files at the same ids, so their meanings are confirmed.

## Device dry-run (2026-10-25): HWSC confirmed

HWSC (SDK NorHwsc.bin) loaded to 0xbfc2c000 and called at 0xbfc2c000 via an ADFU stub that
restores SPI0/MMU and returns to the ROM ADFU loop. Status (36 B): "HW" + flash_id **c8 40 15**
= GD25Q16 — matches the device BREC chipinfo and the SDK nor_id record. So the SDK loaders run on
this unit and identify the chip. HWSC and FWSC share load slot 0xbfc2c000 (both have absolute refs
to 0xbfc2c000), so they are run in separate passes. build_fw.py AFI addresses corrected to
0xbfc2c000 for both. FWSC init pass pending (run `adfu_stub.py fwsc` as a fresh process).

## FWSC init confirmed (2026-10-25)

FWSC (patched NorFwsc.bin, operate=0, chipinfo@112, decrypt=0, chipid table=0) loaded to 0xbfc2c000,
init called at 0xbfc2c200. Returned 36-B status: ST_FWSC status field (+24) = **0 (OK)**,
logic_cap (+20) = 0x1000, no_flash/chipid-mismatch not set. So FWSC init runs, sees the flash, and
the chip-id check passes with our zero chipid header. Both scan loaders are validated on the device.
Only the FWSC driver write (erase+program+CRC) remains untested — it is the same image, reached via
the driver entry +0x400 which we did not call.

## VRAM physical location (static RE, 2026-10-25)

Code: BREC runs XIP at 0x03000000 (= NOR 0xfc0) and copies its SPI driver from NOR 0x2f3c to RAM at 0xbfc39200 (ops table: +0 read 0xbfc39605 (cmd 0x0B, raw 24-bit address), +4 write 0xbfc39765, +16 erase 0xbfc39261, +24/+28 lock/unlock). nor_ud.drv text = NOR 0xbf00 → 0x10006580. Its ops table is at 0x100081d8: +12 ioctl 0x100078c1, +16 read 0x10007811, +20 write 0x10007851, +24 erase 0x10007769. Shared RAM globals: 0x9fc21800 (nor info, +8 = chip bytes), 0x9fc219b0 u8 flags[8], 0x9fc219b8 u32 base[8] (physical byte address of each region type).

**Layout set once by BREC** at 0x030004cd(cap, stg, brec_phys, mode). Called from 0x03001cea with cap = BREC.chipinfo[+4] << 9 = 0x1000 << 9 = **0x200000**, stg = BREC+24 spi_stg_cap = **0xF0000**, brec_phys = (BREC+8 >> 6) * 66 = 0x3456. Flags come from 0x03000469: flags[1..5] = 3, flags[6] = flags[7] = 0. CONFIRMED.

| type | base (physical) | value here | flags |
|---|---|---|---|
| 2 | 63*66 = 4158 | 0x103e (BREC) | 3 |
| 3 | 4158 + brec_phys | 0x4494 = phys(logical 0x4280), the LFI | 3 |
| 4 | ceil(stg/66)*66 | 0xF001E (bank-B BREC) | 3 |
| 5 | base4 + brec_phys | 0xF3474 (bank-B LFI) | 3 |
| 6 | 2*stg | **0x1E0000** (sys_vm VRAM) | 0 |
| 7 | cap - 0x10000 | **0x1F0000** (second 64 KB store) | 0 |

- **Addressing**:
  - nor_ud read 0x10007811 does `addr = addr/64*66` only if flags[type] & 2. It then adds base[type] (0x1000773d).
  - The write path 0x100076d1 sets 0x9fc21801 = 66 (CRC line mode) only if flags & 1, otherwise 0 (plain bytes).
  - Types 6 and 7 have flags 0, so **VRAM is raw physical bytes: no CRC, no 64→66 scaling**. CONFIRMED.
- **sys_vm**:
  - read 0x10008069 = ops.read(..., type 6);
  - write 0x100067b5 / erase 0x10006771 use base[6] + addr;
  - size 0x10008055 = base[7] - base[6] = **0x10000**.
  - So **VRAM = physical 0x1E0000..0x1F0000** (VRAM start = 2 × spi_stg_cap). CONFIRMED.
  - Type 7 = 0x1F0000..0x200000: ioctl 1 erases it whole (0x100078ad: erase(0, 0x10000, 7)), and the type-7 write 0x10007851 pre-erases it. Purpose unknown (INFERRED: a separate BT/config store).
- The device dump (dumps/extract2/nor_0x100000.bin) agrees:
  - data only in 0x1E3F84..0x1EFABC (paired names, 0x55ab records) and 0x1F0F84..0x200000;
  - FF everywhere between 0xF0000 and 0x1E3F84;
  - the region boundary falls exactly on 0x1F0000;
  - repeated records at different 64-byte alignments show no 2-byte shifts.
  - So at these addresses the window returned raw physical bytes 1:1. SPI0+0x20 = 0x1E0F80 at ADFU entry (it looks like the last SPI address, which is inside VRAM).
  - Open question: the LFI reads (phy = logical) imply CRC stripping. Re-read logical 0x4280 with the same session/method as the 0x100000 dump to see whether stripping is address- or session-dependent. The verdict below does not depend on this.
- "ACTS SAFE VRAM" (nor_ud 0x100067f5 / 0x10006841, export 0x10006a05) is a small key/value store at **physical 0x4F0..0x800 (type 0, base 0)**, inside the boot block. It is all FF on this device (nothing stored). Any FWSC flash erases it (first-call erase of 0..0xFFFF). Item id 5 there is read by BREC 0x03001c1c to decide an A/B bank swap (0x030005b1). If the item is absent, BREC boots bank A.
- **Overlap verdict**:
  - Stock LFI ends at logical 0xE2240 = phys 0xE9352.
  - Ours ends at logical 0xEDB80 = phys **0xF525C** (exact; the 0xF4B00 / 0xE9265 estimates were slightly off).
  - VRAM starts at 0x1E0000: our image leaves 0xEADA4 B (≈940 KB) of margin.
  - **No overlap with VRAM (0x1E0000..0x1F0000) or type 7 (0x1F0000..0x200000), for either image**. CONFIRMED.
- **Bank-B caveat** (INFERRED risk, low): the BREC layout reserves bank B from phys 0xF001E (= logical 0xE8BC0). Stock ends below it; ours runs 0x523E B past it (into the bank-B BREC/LFI slot).
  - Nothing is there on the device (all FF).
  - Bank B would be used only by the in-firmware dual-bank update code in nor_ud (export 0x10006d25 writes type 4) or by a SAFE-VRAM id-5 swap record. Neither is used by the card/ADFU flash path.
  - To stay strictly inside bank A, the LFI must end at logical ≤ 0xE8BC0 (stock fits; ours exceeds it by 0x4FC0 = 20 416 B).
- **NorFwsc LFI write erase** (0xbfc2f605 → write 0xbfc2fa95, then erase-ahead 0xbfc2f469). CONFIRMED:
  - Erase state is at 0xbfc2fc08: +4 = last erased 64 KB block, +8 = limit = phys(lfi_cap + LFI base), set in the BREC write 0xbfc2f529.
  - After each chunk, if the block cursor < phys(end), it erases the next 64 KB block, but only while block start < limit, and only if the block is not blank (0xbfc2f2dd).
  - Total erase = 0..0xFFFF (first call) plus 64 KB blocks up to the one containing the LFI end: ≤ 0xEFFFF for stock, **≤ 0xFFFFF for ours**.
  - Full-chip erase happens only if header[2] == 2 (we set 0). VRAM at 0x1E0000+ is never erased.

## Post-flash no-boot analysis (static, 2026-10-25)

**Most likely cause: BREC/LFI written at the wrong offset.** CONFIRMED by code.
- The SDK NorFwsc BREC write (0xbfc2f529) always writes at logical `addr + 1024`. On the first chunk it also sets the LFI base 0xbfc2fd98 = BREC[8] + 1024. The LFI write (0xbfc2f605) writes at `addr + [0xbfc2fd98]`. With the device BREC (BREC[8] = 0x32c0) this gives BREC at logical 0x400 and LFI at 0x36c0.
- The SDK NorMbrec (afi_base) maps BREC at MMU phy = MBREC+4 (= 0x400, which the FWSC MBREC write forces), so the SDK set is self-consistent.
- The device MBREC is a newer dual-bank build that differs from the SDK one in 624 bytes. Its main routine (0x02000171) probes BREC through 0x020000e5(phys) at phys **4096** (MMU phy = ceil(4096/66)*64 = logical **0xfc0**) and at phys MBREC+8 = **0xF0000** (bank B). The device BREC also hard-codes its base as 63*66 and the LFI as base + brec_phys.
- After our flash, logical 0xfc0 holds BREC bytes from offset 0xbc0 onward (bad header/checksum) and bank B is blank, so the MBREC falls back and the ROM enters ADFU. Even if the BREC were found, the LFI would be 0xbc0 too low.
- MBREC write (0xbfc2f4e5): for a 0x5A MBREC it checksums 1022 bytes from +2 and forces +4 = 0x400. The device MBREC already has 0x400 at +4 and its checksum matches (0x5186), so the write left the MBREC content unchanged. CONFIRMED.
- ADFUS 0x10 handler: the address advances by the chunk length for each 8 KB chunk (0xbfc2873e). CONFIRMED.

**Randomizer/scrambler: unlikely to be the cause.**
- 0xbfc2e9e5 (called from init) sets F[0] bit 7 (0xbfc2fc60) unconditionally: `F0 = (F0 & 0xB0) | 0x80`. It does not depend on hdr+16, +3, +28, boot_config, bonding or chipid. F[1] = 66 (CRC line). CONFIRMED.
- fa95 sets SPI0 CTL |= 0x2000, then |= 0x8000, for the whole write and clears both at the end.
- Page program 0xbfc2f7c9 clears 0x8000 for the duration of the page, pulses 0x2000 (off, then on) at every 66-byte line boundary (counter 0xbfc2fc5c), and sets 0x8000|CS afterwards.
- The device's own BREC NOR driver write 0xbfc39765 (NOR 0x2f3c → RAM 0xbfc39200) runs the **identical** per-line 0x2000 pulse / 0x8000 sequence whenever its line length (0x9fc21801) is 66. So the hardware treatment matches the stock writer. CONFIRMED.
- The CRC16 (poly 0x1021, init 0xFFFF, byte-swapped) is computed in software (0xbfc2f6a1).
- No key register is written during writes. Init writes hdr chipid[idx] to SPI0+0x14 with CTL|0x4000, and that value is 0 = live value. The bit meanings themselves are INFERRED.
- Live CTL 0x00361838 has bits 3-5, 11, 12, 17, 18, 20, 21 set; 0x2000/0x8000/0x4000 are clear. That is the idle state, consistent with the bits being set only during writes.

**Raw physical read after init only.**
- The 0xbfc2d68d / ctx 0xbfc2d564 library targets a controller at 0xc00f0000 and has no callers except an ID probe. Do not use it. CONFIRMED.
- The real SPI0 read is ops[0] of table 0xbfc2cc30 = **0xbfc2d065(a0 = physical 24-bit byte address, a1 = buf, a2 = len)**, MIPS16 (call address|1 via jalr from MIPS32; it returns with jrc ra). It sends cmd 0x0B + 3 address bytes + 1 dummy byte and reads by PIO in chunks of ≤ 0x8000 (BC at +0x10). It does no 64→66 translation, so the result is raw physical bytes including the CRC words.
- It brackets the access with 0xbfc2cce5/0xbfc2cd51: these save CTL into F[12], clear CTL bits 0xC01 (and 0x1000 when F0 bit 7 is set), and restore CTL afterwards. They do not touch 0x2000/0x8000, so a caller could set those bits beforehand to experiment (INFERRED).
- Required state: init only. Init sets F0 and zeroes the nest counter F[4]; F0 bit 6 is clear until the first driver call (0xbfc2dc8d), so d065 skips the 0xbfc2cf91 poll. The driver +0x400 must still never be called.
- Buffers: 0xbfc29000..0xbfc2bfff is proven. 0xbfc30000..0xbfc3ffff is very likely free in ROM ADFU, since FWSC uses 0x9fc30000 only in its write path (INFERRED).
- XIP hang from a cold ADFU (INFERRED): init 0xbfc2c67d also programs the CRC/raw boundary (SPI0+0x20, 0xc0140008, 0xc014000c = 0x3fffc0, 0xc0140000 |= 4) and clocks (0xc0001004, 0xc0000000, 0xc0001068), and _restore_regs does not restore any of these. In addition, reading lines with bad CRC through the MMU may stall the bus. Capture 0xc0140000..+0x10 and those CMU registers in both the warm and cold states.

**Recovery write options** (needs review before any device use):
- (a) Patch the three `+1024` immediates in 0xbfc2f529 (0xbfc2f534, 0xbfc2f556, 0xbfc2f5d6) to 0xfc0 in the uploaded NorFwsc copy. BREC then lands at 0xfc0, the LFI base becomes BREC[8] + 0xfc0 = 0x4280, and the erase limit is derived correctly. Keep the device MBREC.
- (b) Or use the SDK NorMbrec, which matches the SDK FWSC layout. However, the device BREC hard-codes base 63*66, so (b) also needs the SDK NorBrec and a matching LFI. Not recommended.
- Plain vs scrambled: no header field disables the 0x2000/0x8000 handling. It matches the stock driver anyway, so no change is needed.

## Factory BREC NOR driver, fully reversed (static, 2026-10-25)

Source for everything below: `dumps/nor_logical_1MB.bin` (BREC 0x0fc0..0x4280, runs XIP at
0x03000000), `dumps/extract/xip_0x10000000.bin` (nor_ud), `dumps/extract/kseg1_0xbfc00000.bin`.
Disassembly: objdump `-b binary -m mips:16 -EL`, blob at vma 0xbfc39200, BREC at vma 0x03000000.

### Blob extent and installation — CONFIRMED

BREC `0x030019e1` installs it:

```
30019e0: save 24,ra
30019e2: lw a1,[0x3001a08]=0xbfc002b8   ; ROM memset slot
30019e4: lw v1,[0x3001a0c]=0x9fc21800
30019e6: lw a3,[0x3001a10]=0x9fc21a64
30019e8: lw v0,0(a1)
30019ea: subu a2,a3,v1                  ; 0x264
30019ec: move a0,v1
30019ee: jalr v0                        ; memset(0x9fc21800, 0, 0x264)
30019f2: lw a0,[0x3001a14]=0xbfc002bc   ; ROM memcpy slot
30019f4: lw v1,[0x3001a18]=0xbfc39200   ; dest
30019f6: lw a2,[0x3001a20]=0xbfc3a010   ; dest end
30019f8: lw v0,0(a0)
30019fa: lw a1,[0x3001a1c]=0x03001f7c   ; src = NOR logical 0x2f3c
30019fc: move a0,v1
30019fe: jalr v0
3001a00: subu a2,v1                     ; len = 0xE10
```

- **blob = nor_logical_1MB.bin[0x2f3c:0x3d4c], 0xE10 = 3600 B, load address 0xbfc39200.**
  The last 16 B are a literal pool (0xc0090020/24/2c/30); 0x3d4c.. is zero. Position-**dependent**:
  the ops table at the blob start holds absolute 0xbfc39xxx pointers.
- ROM slots used: `*(0xbfc002b8)`=0xbfc00855 (memset), `*(0xbfc002bc)`=0xbfc00895 (memcpy),
  and the blob itself calls `*(0xbfc002bc)` (ops+0x20) and `0xbfc0421c` (ROM delay, from the
  pin-mux routine). All three exist in the ROM dump.

### Ops table at 0xbfc39200 — CONFIRMED (the old note's mapping was wrong)

| off | addr | function |
|---|---|---|
| +0x00 | 0xbfc39605 | `read(phys_addr, buf, len)` — cmd 0x0B, raw bytes |
| +0x04 | 0xbfc39765 | `write(phys_addr, buf, len)` — page program |
| +0x08 | 0 | alternate write (selected by `info[0]&0x0f`, never used: must stay 0) |
| +0x0c | 0xbfc39575 | **`erase(phys_addr)`** — opcode from `info[3]` |
| +0x10 | 0xbfc39261 | `spi_enter()` (nest counter `info[4]`, saves CTL to `info+12`) |
| +0x14 | 0xbfc392b5 | `spi_exit()` (restores CTL) |
| +0x18 | 0xbfc39870 | `irq_off()` → old CP0 Status (MIPS32: `di; jr ra`) |
| +0x1c | 0xbfc3987c | `irq_restore(a0)` (MIPS32: `mtc0 a0,$12; jr ra`) |
| +0x20 | 0xbfc39251 | call `*(0xbfc002bc)` |
| +0x24 | 0xbfc39241 | `jrc ra` (no-op) |
| +0x28 | 0xbfc3932d | `cmd_read1(cmd)` → 1 status byte (RDSR) |
| +0x2c | 0xbfc3943d | `send_bytes(buf, len)` |
| +0x30 | 0xbfc394a1 | `wait_wip()` = while (RDSR(0x05) & 1) |
| +0x34 | 0xbfc394b5 | `cmd_read4(buf, cmd)` → 4 bytes (RDID 0x9F / 0x90) |

All entries are MIPS16 (`addr|1`). Register blocks: **SPI0 = 0xc0110000** (+0 CTL, +4 STAT,
+8 TXDAT, +0xc RXDAT, +0x10 BC, +0x20 "active LFI logical base"), **NOR cache/XIP ctl = 0xc0140000**
(+0 ctl, +4 invalidate, +8 size, +0xc addr mask), CMU 0xc0000000/0xc0001004/1028/1064/1068,
pin mux 0xc0090018/1c/3c/40.

### Shared globals — CONFIRMED

`0x9fc21800` nor info struct (zeroed by the BREC installer above):

| off | meaning |
|---|---|
| +0x00 | caps byte. `&0x40` → poll WIP *before* the op instead of after; `&0x0f` selects ops+8; `&0x81` extra CTL mask in spi_enter |
| +0x01 | **line length: 0 or 66** |
| +0x02 | page-program opcode (0x02) |
| +0x03 | erase opcode (block erase; callers temporarily set 0x20 for 4 KB) |
| +0x04 | spi_enter nest counter |
| +0x08 | chip capacity in **bytes** (bounds check in nor_ud 0x1000773d) |
| +0x0c | saved CTL (u32) |
| +0x10 | flags: `0x40` = chip ≤ 4 MB / line mode usable; `0x08` = skip type-7 pre-erase |
| +0x11 | capacity % 66 |

Region tables: `0x9fc219b0` u8 flags[8], `0x9fc219b8` u32 base[8] (physical byte base),
`0x9fc219d8` = spi_stg_cap, `0x9fc21a00` u8 clock/mode, `0x9fc21a04` u32 capacity in bytes
(must be set *before* the probe; the driver does not derive it).

### Q1 — where the 66 comes from — CONFIRMED

`0x9fc21801` is a **global, rewritten on every call** by the generic rw wrapper, from the
*region* flag bit 0. Two byte-identical copies of that wrapper exist: BREC `0x0300073d` and
kernel nor_ud `0x100076d1`:

```
100076d0: save a0-a2,32,ra,s0-s1        ; a3 = flags[type]
100076d4: lw v1,[0x10007734]=0xbfc39200
100076d6: lw v0,24(v1)                  ; ops+0x18 irq_off
100076d8: jalr v0
100076da: move s0,a3
100076de: li v0,1
100076e0: and v0,s0                     ; flags & 1
100076e2: beqz v0,0x1000770d            ; -> store 0
100076e4: lw v1,[0x10007738]=0x9fc21800
100076e6: li a1,15
100076e8: lbu a0,0(v1)
100076ea: li v0,66
100076ec: and a0,a1                     ; info[0] & 0x0f
100076ee: sb v0,1(v1)                   ; *** info[1] = 66 ***
100076f0: bnez a0,0x10007719            ; ops+8 if info[0]&0xf, else ops+4
...
1000770c: lw v1,[0x10007738]
1000770e: li a1,15
10007710: lbu a0,0(v1)
10007712: sb v0,1(v1)                   ; *** info[1] = 0  (v0 == 0 here) ***
```

- Only two values exist in the whole firmware: **66 and 0**. No other store to 0x9fc21801 exists
  in the BREC, in the blob, or in nor_ud.
- So line mode is **per region, applied as a global**: `flags[type] & 1`.
- Address scaling is a *separate* flag bit: nor_ud read `0x10007811` does `addr = addr/64*66`
  only when `flags[type] & 2` (`srl v0,a0,6; sll a0,v0,5; addu a3,a0,v0; sll a0,a3,1`), then
  `0x1000773d` adds `base[type]` and bounds-checks against `info[8]`.
  **nor_ud write `0x10007851` does NOT scale the address or the length** — its callers pass
  physical values. Lengths are never scaled on either path.
- flags/base are set once by BREC `0x030004cd` (values already tabulated above: types 2..5
  flags 3, types 6 and 7 flags 0, base[6]=2*stg=0x1E0000, base[7]=cap-0x10000=0x1F0000).
  Verified pools: 0x30005a4 = 0x3e0f83e1 (the /66 magic multiplier), 0x30005a8 = 0xffff0000,
  0x30005ac = 0xc0110020.
- **The "raw area at the end of the chip" is purely a software property** (flags 0 → line
  length 0 → the driver never touches the CRC/scramble bits). The boundary is `base[6]`/`base[7]`
  in the software table, not a hardware register.
- The *hardware* boundary registers are programmed by the blob's probe `0xbfc39af5`:

```
bfc39b42: lw a3,[0xbfc39b8c]=0x3e0f83e1
bfc39b44: lw a0,[0xbfc39b88]=0x9fc21810
bfc39b46: multu a1,a3                   ; a1 = capacity bytes (*(u32*)0x9fc21a04)
bfc39b4a: mfhi v1
bfc39b4e: srl a3,v1,4                   ; a3 = cap/66
bfc39b50: or a2,64                      ; info[0x10] |= 0x40
bfc39b5c: sb v0,1(a0)                   ; info[0x11] = cap % 66
bfc39b5e: lw a2,[0xbfc39b90]=0xc0110020
bfc39b60: lw a0,[0xbfc39b94]=0xc0140008
bfc39b62: sll v1,a3,6                   ; v1 = (cap/66)*64  = logical capacity
bfc39b64: lw v0,[0xbfc39b9c]=0x003fffc0
bfc39b66: lw a3,[0xbfc39b98]=0xc014000c
bfc39b68: sw v1,0(a2)                   ; SPI0+0x20   = logical capacity
bfc39b6a: sw v1,0(a0)                   ; 0xc0140008  = logical capacity
bfc39b6e: sw v0,0(a3)                   ; 0xc014000c  = 0x003fffc0
bfc39b70..76: *(0xc0140000) |= 4        ; line/CRC mode enable
```
  (capacity > 4 MB takes the other branch: `info[0x10] &= ~0x40` and none of these registers
  are written, i.e. no line mode on a >4 MB chip.)
  This is **identical to what SDK NorFwsc init (0xbfc2c67d) does** — same four registers, same
  0x3fffc0, same `|= 4`. SPI0+0x20 is additionally rewritten by BREC `0x030004cd`/`0x030005b1`
  with `(base[active LFI]/66)*64` = the **logical** address of the active LFI (0x4280 for bank A),
  which is why the live value read at ADFU entry (0x1E0F80) is neither of those: it is whatever
  the last NOR access left there. Exact semantics of SPI0+0x20: UNKNOWN.

### Q2 — the 2 extra bytes per line: hardware, not software

- The write routine `0xbfc39765` is a **pure byte blaster**. It pushes exactly `len` bytes from
  the caller's buffer, 66 of them between pulses; it computes nothing:

```
bfc39764: save a0-a2,48,ra,s0-s1        ; a0 addr, a1 buf, a2 len
bfc39768: move v1,a0 ; zeb v1           ; page offset = addr & 0xff
bfc3976c: lw s0,[0xbfc39860]=0x9fc21800
bfc39770: jal 0xbfc39261                ; spi_enter
bfc39774: lbu s1,1(s0)                  ; s1 = line length (66) -> boundary hits immediately
bfc39778: beqz a0,0xbfc3984d            ; len == 0
; per 256-byte page chunk:
bfc39782: sltiu v0,257                  ; chunk = min(len, 256 - pageoff)
bfc39794: jal 0xbfc39715                ; WREN + info[2] + 24-bit address
bfc3979c: lbu a0,1(a1)                  ; line length
bfc3979e: beqz a0,0xbfc397af
bfc397a2: li a3,32769 ; neg a3           ; ~0x8000
bfc397a8: lw a2,0(v0) ; and a2,a3 ; sw  ; *** CTL &= ~0x8000  (engine ON) ***
; per byte:
bfc397bc: lbu v0,1(a1)                  ; line length
bfc397be: beqz v0,0xbfc397c5            ; 0 -> plain byte, no CTL touch at all
bfc397c0: xor v0,s1
bfc397c2: beqz v0,0xbfc397e5            ; s1 == 66 -> line boundary
bfc397c6: li v1,32
bfc397c8: lw v0,0(a0) ; and v0,v1
bfc397cc: bnez v0,0xbfc397c9            ; while (STAT & 0x20)
bfc397d2: lbu a0,0(a1)
bfc397d8: sw a0,0(a3)                   ; TXDAT = *buf++
bfc397dc: addiu s1,s1,1
; line boundary:
bfc397e4: jal 0xbfc392ed                ; wait TX idle
bfc397ec: li a0,8193 ; neg a1,a0
bfc397f4: and a3,a1 ; sw                ; *** CTL &= ~0x2000 ***
bfc397fa: addiu a1,16385                ; a1 = 0x2000
bfc397fe: or s1,a1 ; sw                 ; *** CTL |=  0x2000 ***
bfc39802: li s1,0                       ; restart the line byte counter
; after each chunk:
bfc39814: lbu a2,1(a1)
bfc39816: beqz a2,0xbfc39849
bfc3981e: or v0,0x8000 ; sw             ; *** CTL |= 0x8000 (engine OFF) ***
bfc39824: or a2,8 ; sw                  ; CTL |= 8
```
- **CONFIRMED from the flash contents:** the check word actually stored is *not* the software
  CRC the SDK loader put in the buffer. For the 16 MBREC lines (logical content unchanged by
  our flash, so plaintext known) the stored tail matches neither `crc16_sdk(plain)` nor
  `crc16_ccitt(plain)` nor the same over the stored bytes, in either byte order; and
  `tail ^ crc(buf, init)` is not constant across lines, so it is not a CRC of those 64 bytes
  under *any* init either.
- **CONFIRMED:** for the two all-zero lines (logical 0x380..0x400) the stored tail is exactly
  `d6 da` = CRC-16/CCITT (poly 0x1021, init 0xFFFF) of 64 zero bytes, stored **big-endian**
  (high byte at line offset 64). Both zero lines, at different addresses, give the same tail →
  the codec is **address-independent**.
- **CONFIRMED:** there is no CRC16 anywhere in the factory write path — not in the blob, not in
  BREC's wrapper, not in nor_ud. No 0x1021/0x8408 constant and no CRC16 table exists in
  `xip_0x10000000.bin`, `nor_logical_1MB.bin`, `nor_ud.drv`, `NorBrec.bin` or `kernel.drv`
  (the only CRC-16/CCITT nibble table in the system is in the boot ROM at 0xbfc027f0, used
  solely by the SD/MMC block path 0xbfc020c5 → 0xbfc020fd).
- **INFERRED (strong):** the hardware line engine generates the check word; the 2 bytes software
  pushes at line offsets 64/65 are placeholders and are discarded/overwritten. The alternative
  (hardware only scrambles bytes software supplies) would require the factory firmware to compute
  the CRC for its own bank-B line-mode writes, and it demonstrably cannot.

### Q3 — the data transform: hardware, measured

The transform is in the **SPI0 data path**, gated by CTL bits, and is measurable from data we
already hold:

- `dumps/raw_0x000000_420_m0.bin` vs `..._m2000.bin`: **1053 of 1056 bytes differ** — i.e. setting
  CTL 0x2000 changes what a plain 0x0B PIO *read* returns. `..._m8000.bin` and `..._ma000.bin` are
  **byte-identical to m0**: so **0x8000 overrides/bypasses 0x2000**. CONFIRMED.
- That is exactly the state the writer arranges: during data bytes 0x8000 is clear and 0x2000 is
  set (pulsed off/on at every 66-byte boundary → per-line state reset); afterwards 0x8000 is set
  again. When `info[1] == 0` the writer never touches either bit, so the engine stays bypassed
  → **plain 1:1 bytes**. This is the mechanism behind the plain BT/VRAM regions. CONFIRMED.
- Characterising the codec from the 16 MBREC line pairs (plain = nor_logical_1MB.bin[0:0x400],
  stored = raw_0x000000_420_m0.bin):
  - `stored[0]` is always `plain[0]`; the first differing byte is always a XOR of exactly 0x02,
    at a line-dependent position (reproduces the previously reported observation).
  - The XOR masks of different lines share long prefixes and diverge where the *stored* bytes
    diverge, e.g. line 1 mask[3..] == line 2 mask[1..] (both lines contain `a0 e8` at those
    positions).
  - Fitted model, **0 conflicts in 1024 samples, 873 distinct keys**:
    **`stored[k] = plain[k] XOR F(stored[k-1], stored[k-2], stored[k-3])`**, with
    `stored[<0] = 0` and `F(0,0,0) = 0`. With only 2 bytes of history there are 2 conflicts
    in 1024; with 3 bytes, none; input-byte history (`in[k-1..k-3]`) gives 145 conflicts, so the
    feedback is from the **stored** stream, not the plaintext.
  - `F` is **not** GF(2)-linear: Gaussian elimination over GF(2) for `mask[k] = Σ d_t · x[k-t]`
    with x = stored or plain, t ≤ 16/24/32/64, both bit orders, is full rank with ~3400-3500
    inconsistent equations every time. So it is a small nonlinear (S-box-like) feedback function
    of 24 bits of history, not an LFSR scrambler. Its table is UNKNOWN (873 of 2^24 entries
    sampled).
  - No key material is involved on this device: the blob never writes SPI0+0x14 or SPI0+0x18 and
    never sets CTL 0x4000 (verified by scanning every 32-bit literal in the blob: it references
    only 0xc0110000/4/8/c/10/20 and 0xc0140000/4/8/c). `F(0,0,0)=0` and the address-independence
    are consistent with "no key / key 0".
- **Consequence (this is the point): the codec is transparent to software.** Because the feedback
  is taken from the *stored* bytes, the decoder is simply `plain[k] = stored[k] ^ F(stored[k-1..k-3])`
  — the exact inverse of the encoder. The fact that one single `F` fits all 16 lines, derived from
  *factory-decoded* plaintext against *SDK-FWSC-written* stored bytes, means the encoder the SDK
  loader drove is the inverse of the decoder the boot ROM/MMU uses. **The SDK loader's bytes land
  in flash correctly encoded.** The transform is therefore *not* a cause of the no-boot.
- Also confirmed empirically for the plain regions: a paired phone's name (redacted) sits at physical **0x1F4B1E** in
  `dumps/raw_0x1e0000_1f008_m0_170705.bin`, at a non-64/66-aligned offset, plain ASCII → type 7
  (flags 0) really is written with the engine bypassed.

### Q4 — factory driver vs SDK NorFwsc write path

Behavioural differences that could change bytes in flash: **none at the hardware level.**

| aspect | factory BREC driver | SDK NorFwsc | same? |
|---|---|---|---|
| line length | `info[1]` = 66 (flags&1) or 0 | F[1] = 66, set unconditionally by 0xbfc2e9e5 | yes for line regions; the SDK has **no plain mode** |
| per-line engine pulse | CTL &= ~0x2000 then \|= 0x2000 every 66 B | identical (counter 0xbfc2fc5c) | yes |
| engine enable/bypass | CTL &= ~0x8000 per page chunk, \|= 0x8000 after | identical (fa95/f7c9) | yes |
| check word | nothing; hardware generates | computes CRC-16 in software (0xbfc2f6a1, poly 0x1021, init 0xFFFF, bits fed LSB-first, result byte-swapped) and puts it in the buffer — **overwritten by hardware, harmless** | effectively yes |
| boundary registers | SPI0+0x20, 0xc0140008 = (cap/66)*64; 0xc014000c = 0x3fffc0; 0xc0140000 \|= 4 | identical values | yes |
| key / chipid | never writes SPI0+0x14, never sets CTL 0x4000 | writes hdr chipid to SPI0+0x14 with CTL\|0x4000 — value 0 = live value | difference exists but is a no-op here |
| page program | 256-byte pages, opcode `info[2]`, 24-bit address | same | yes |
| erase | ops+0x0c with opcode `info[3]`; callers use 0x20 (4 KB) or the chip's block opcode (64 KB) | 64 KB blocks only (0xbfc2df35 walks in 64 KB) | SDK is coarser |
| alignment requirement | `s1` is seeded with the line length so the first byte pulses: the address **must** be 66-aligned and the length a multiple of 66 | same | yes |
| **offsets** | BREC base hard-coded 63*66, LFI = base + brec_phys | BREC at `addr+1024`, LFI base = BREC[8]+1024 | **NO — this is the bug** |

So the previously recorded conclusion stands and is now backed by the device's own driver: the
only thing the SDK loader got wrong is the **record offset** (BREC at logical 0x400 / LFI at
0x36c0 instead of 0xfc0 / 0x4280). Nothing about the CRC, the scrambler, the key, or the register
order differs in a way that changes the stored bytes.

### Q5 — uploading the factory driver over ADFU: possible, but pointless

Everything needed is known:

- blob: `nor_logical_1MB.bin[0x2f3c:0x3d4c]`, 3600 B, **must** load at 0xbfc39200 (absolute
  pointers). Free in ROM ADFU (ROM sp = 0xbfc45ff0; the ADFUS stub uses 0xbfc28000 and buffers
  0xbfc29000..0xbfc2bfff).
- init order, copied from BREC 0x03001ca9..0x03001cf0:
  1. `memset(0x9fc21800, 0, 0x264)`;
  2. set `*(u32*)0x9fc21a04` = capacity in bytes (0x200000 here), `*(u8*)0x9fc21a00` = clock/mode,
     `info[2]` = 0x02, `info[3]` = block-erase opcode, `info[0]` = caps (0 works);
  3. `0xbfc39a95(1)` — CMU: 0xc0001004 |= 0x4001, 0xc0000000 &= ~2 then |= 1, 0xc0001068 &= ~4;
  4. `0xbfc39af5(0, 7)` — probes 6 clock/mode variants calling `0xbfc39a5d`, each followed by
     RDID (`0xbfc39dc1`: ops+0x34 with 0x9F, falls back to 0x90, rejects 0x00/0xFF); on success
     writes `info[0x10] |= 0x40`, `info[0x11] = cap%66`, and the four boundary registers above.
     Its inner `0xbfc39bed` does the real SPI0 bring-up: `*(0xc0001064) = 0x001fffff`,
     `*(0xc0001028) = 64`, CMU 0xc0000000 bit toggles, `CTL = (div<<16) | 0x00100008`,
     CTL |= 0x100 and wait STAT&0x100 (else 0xc012001c |= 1), `*(0xc0140000) |= 1` +
     `*(0xc0140004) = 1` and wait (XIP cache invalidate), then CTL width/mode bits;
  5. `0xbfc39999(BREC[180])` — pin mux 0xc0090018/1c or 0xc009003c/40 = (x & ~0x7010) | 0x3120,
     ROM delay 0xbfc0421c(10), CTL = (CTL & 0xf7fff3ff) | 0x08000c00 (bus width);
  6. `0x9fc219b0/0x9fc219b8` region tables, or just pass physical addresses directly.
- calling convention: all MIPS16, call as `addr|1`; `write(a0 = physical byte address, a1 = buf,
  a2 = physical length)`, `erase(a0 = physical byte address)` with the opcode in `info[3]`,
  `read(a0 = physical, a1 = buf, a2 = len)`. Wrap write/erase/read in ops+0x18/+0x1c if interrupts
  can fire. `info[1]` must be set to 66 (line regions) or 0 (plain) **before each call**, and for
  66 the address must be 66-aligned and the length a multiple of 66, with 2 placeholder bytes per
  line in the buffer.
- what would have to be faked: nothing exotic — the blob brings up its own clocks, SPI0, pin mux
  and XIP cache; it needs the boot-ROM jump slots 0xbfc002b8/0xbfc002bc and 0xbfc0421c (present),
  the capacity pre-set at 0x9fc21a04, and `BREC[180]` (it dereferences 0x03000000+180 for the pin
  mux, so either keep a BREC image mapped at 0x03000000 or patch that literal at 0xbfc39a24).

**Recommendation: don't.** The device's own driver is bit-for-bit equivalent to NorFwsc's write
path at the hardware level (table above), so uploading it buys nothing over recovery option (a)
already recorded here — patch the three `+1024` immediates in NorFwsc 0xbfc2f529 to 0xfc0. The
factory driver would additionally have to be driven byte-exactly by a custom host protocol
(it has no ADFU command layer of its own), which is strictly more risk for zero gain.

### Corrections to earlier sections of this file

- The ops table mapping "+16 erase 0xbfc39261, +24/+28 lock/unlock" is wrong: **+0x0c is erase**,
  +0x10/+0x14 are spi_enter/spi_exit, +0x18/+0x1c are irq_off/irq_restore.
- "The CRC16 ... is computed in software (0xbfc2f6a1)" is true of NorFwsc but the value never
  reaches the flash; the hardware supplies the check word.

## Boot ROM NOR read path, fully decoded (static RE, 2026-10-25)

Source: `dumps/extract/kseg1_0xbfc00000.bin`. The boot ROM is **only 0xbfc00000–0xbfc08000**
(32 KB); everything from +0x8000 to +0x1f000 in the dump is zero and +0x1f000 upward is RAM, so a
literal-pool scan of the first 32 KB covers the whole ROM. All ROM peripheral access goes through
MIPS16 PC-relative literals (the only MIPS32 `lui` of a peripheral base in the ROM is
`bfc030dc: lui t8,0xc00b`), so that scan is exhaustive.

### Boot flow

| addr | role |
|---|---|
| 0xbfc00070 | reset: sp=0xbfc45ff0, ebase=0xbfc00000, `jalx 0xbfc00618` |
| 0xbfc00619 | main: `0xbfc00331` (early init) → `0xbfc006ad` (eFuse read) → if `ret & 0x2000` `0xbfc01769` → **`0xbfc00511` NOR boot** → `0xbfc005d1` card boot → `0xbfc00501` → `jalx 0xbfc030d0` USB ADFU → hang |
| 0xbfc006ad | eFuse read: `[0xc0100004] &= ~0x801`, `&= ~0x101`, `[0xc0100000] \|= 4`, poll `[0xc0100000]&1`, then up to 5× `[0xc010000c] = 0x004b4559` ("KEY") until `[0xc0100000]&2`, returns `[0xc010000c]` |
| 0xbfc00511 | the NOR boot loop (below) |
| 0xbfc000b4 | six `nop`s then `0xbfc000c8: jr a0` — "jump to the loaded image", never returns |

**0xbfc00511, the NOR boot loop.** `p = *(u32*)0xc00a011c` (= 0xbfc20000 live; a scratch struct
pointer register). Two outer passes over `addr`, six inner attempts each:

```
0xbfc015f5                                  # save CMU regs into 0xbfc20014, [0xc0001028]=0x41,
                                            # [0xc0001004] |= 0x4000, [0xc0001064] |= 0x800
addr = 0
for pass in 0..1:                           # 4(s1)
    p[12] = 0; p[16] = 0
    for i in 0..5:                          # 0(s1), p[12] mirrors i
        0xbfc01481                          # delay(64); [0xc0110000] = 0x00180008;
                                            # 0xbfc010e1(p[12], 1)  -> pad/line config, see below
        0xbfc0152d                          # NOR wake / "exit continuous read"
        0xbfc014b5(addr)                    # cache invalidate + MMU entry 0
        p[16] = 1;  if 0xbfc00fd9() == 0: jalx 0xbfc000b4 with a0 = 0x02000010
        0xbfc014b5(addr)
        p[16] = 0;  if 0xbfc00fd9() == 0: jalx 0xbfc000b4 with a0 = 0x02000010
        p[12]++
    addr = 0xF840                           # li v0,63552 at 0xbfc005ac
0xbfc01699                                  # restore CMU/pads from 0xbfc20014, [0xc0110000] &= ~4
return                                      # -> card boot, then USB ADFU
```

So **24 attempts**: 2 addresses × 6 pin/clock configurations × 2 CRC modes.
`addr = 0xF840 = 993*64`, i.e. the first 64-byte logical line boundary at or after physical 64 KB
(993*66 = 0x10002) — the second MBREC slot, in the second erase block. It is blank on this device.

### (a) CRC-line vs plain — **CRC line (64+2), CONFIRMED by code**

`0xbfc00fd8` (the MBREC read+check):

```
bfc00fdc  [0xc012001c] |= 1
bfc00fe8  v0 = (*(u32*)0xc00a011c)[16]              # the flag the loop sets
bfc00fee  if (v0 != 0) goto bfc01002
bfc00ff0  [0xc0110000] &= ~0x1000                   # SPI0 CTL: plain
bfc01000  goto bfc01010
bfc01002  [0xc0110000] |=  0x1000                   # SPI0 CTL: CRC line
bfc01010  buf = 0x02000000
bfc01014  if (buf[2] == 0xA5) len = 512
bfc01028  elif (buf[2] == 0x5A) len = 1024
bfc0103c  else return 1
bfc01042  return 0xbfc0064d(buf, len)
```

The read is a **cache/XIP read through the MMU window**, never PIO. `0xbfc014b5(phy)`:

```
bfc014c0  delay(16384)
bfc014c6  [0xc0140004] = 1 ; 0xbfc00445(0xc0140004, 1, 0, 5)   # wait for the invalidate
bfc014dc  [0xc0140000] = 1
bfc014e2  for i in 0..15: [0xc00a0090 + 8i] = 1               # invalidate all 16 mappings
bfc01504  [0xc00a0090] = 0x42000000                           # valid | virtual base 0x02000000
bfc0150a  [0xc00a0094] = phy                                  # logical NOR offset
```

**SPI0 CTL bit 0x1000 = the "cache randomize" / CRC-line engine.** Proof: the SDK declares
`void hal_spi_phy_init(bool cache_randomize)`
(`vendor/2819p/psp_rel/include/hal_inc/hal_spi.h:19`), and that function's body is NorFwsc
0xbfc2c840–0xbfc2c87a:

```
bfc2c840  CTL &= ~0x2010
bfc2c84a  if (cache_randomize) { CTL |= 0x3001 ; CTL &= ~0x8001 }   # sets 0x2000 and 0x1000
bfc2c860  else                 { CTL &= ~0x1001 }
bfc2c86c  CTL &= ~0x101 ; poll while (STATUS & 0x201)
```

The ROM's first attempt of each pair has it **set**, the second clear. The successful attempt is
necessarily the one with it **set**, because the device MBREC never touches the SPI controller —
there is no `0xc011xxxx` literal anywhere in its 1024 bytes (its only register literals are
0xc00a0094, 0xc00a0090, 0xc0001034, 0xc0001004, 0xc0020004, 0xc002003c, 0xc0090028, 0xc0020010,
0xc012001c) — and yet it converts its physical BREC address to an MMU `phy` by `ceil(phys/66)*64`.
The 66→64 translation must therefore already be live when the ROM jumps to it.
**The MBREC must be stored as 66-byte CRC lines, exactly like the BREC and the LFI.**
The plain pass exists only as a fallback (and is harmless: for a CRC-line chip the raw byte at
physical 2 is not 0xA5/0x5A, so it is rejected immediately).

### (b) scrambler / key — **randomizer yes, key no. CONFIRMED**

- `0xc0110014` (SPI0+0x14), `+0x18`, `+0x1c`, `+0x20`, `+0x24` appear in **no** literal pool in
  the 32 KB ROM. The ROM touches SPI0 only at +0x00 (CTL), +0x04 (STATUS), +0x08 (TXDAT, one
  single write of the constant 0xFF) and +0x10 (BC, one reference, in the card path).
- **Correction to the earlier note:** `0xc0110014` is referenced nowhere in `NorFwsc.bin` either.
  The `&= ~0x4001` / `|= 0x4000` sequence at 0xbfc2c7a2 is on **0xc0000000 (CMU)**, not on
  SPI0+0x14 — there is no key/chipid write to the SPI controller at all.
- The transform itself is **unkeyed and address-independent**: a GF(2) solve of
  (64 plain bytes) → (66 physical bytes) over the 3929 distinct lines of
  `dumps/raw_ref_sdk_layout_0x40008.bin` has **rank 513 with zero inconsistent equations**, the
  additive constant on the 64 data bytes is exactly 0, and the resulting closed form reproduces
  the 14476 lines of the independent later dump `raw_0x000000_e9418_m0_164007.bin` bit-for-bit.

**The physical format, solved exactly** (implemented and self-testing in `tools/norline.py`):

```
phys_line(66) = scramble( plain[0:64] || crc16(plain) )
  crc16  : poly 0x1021, init 0xFFFF, data bits fed LSB-first, result byte-swapped, stored LE.
           This is NorFwsc 0xbfc2f6a1 (and the stock BREC driver's) CRC, computed in software.
           crc16(64 zero bytes) = 0xdad6, which is exactly the solved affine constant.
  scramble: g[m] = parity(src[m] & 0x3C)           one feedback bit per *source* byte
            ks[k] bit j = XOR of g[k-t] for t in LAGS[j], t <= k
            out[k] = src[k] ^ ks[k]                k = 0..65
            The 2 CRC bytes are scrambled too and also feed g.
```

Consequences worth knowing: `out[0] == plain[0]` always, and the first differing byte in a line is
always a XOR of exactly 0x02 (that is `ks` bit 1, tap lag 1, i.e. `g[k-1]`) — which is the pattern
seen in the raw dumps. The scrambler is *causal*, so decoding needs no inversion: recover each
plain byte, then feed it back. It is **not** a bit-serial LFSR (no shift-invariant bit-level
recurrence exists, which is why the earlier 64-tap GF(2) fit failed); it is byte-clocked, one
feedback bit per byte, with 8 independent tap sets.

### (c) CRC-area / raw-area boundary — **the ROM programs none. CONFIRMED**

- The ROM writes only `[0xc0140004] = 1` and `[0xc0140000] = 1` (cache invalidate, in 0xbfc014b5).
  `0xc0140008` and `0xc014000c` are referenced **nowhere** in the ROM, and neither is SPI0+0x20.
- NorFwsc init does program them, at **0xbfc2c6b2** (the ≤4 MB branch of 0xbfc2c67d):

```
L = floor(capacity / 66)                       # multu by 0x3e0f83e1 (= 16/66), hi >> 4
F[5] (0xbfc2fc74) |= 0x40 ; *(u8*)0xbfc2fc75 = capacity - L*66
[0xc0110020] = L * 64
[0xc0140008] = L * 64
[0xc014000c] = 0x003fffc0
[0xc0140000] |= 4
```

- **SPI0+0x20 is the logical end of the CRC area.** Measured, not guessed: the live firmware value
  is 0x1E0F80 = `floor(0x1F0000/66)*64` (0x1F0000 = cap − 0x10000 = the type-7 store base), and
  the window↔raw alignment breaks exactly there. Raw physical 0x1F3000 == window address 0xE3F84
  (`dumps/raw_0x1e0000_1f008_m0_170705.bin` vs `dumps/extract2/nor_0x100000.bin`), a constant
  delta of 0x10F07C, and `0x10F07C = 0x1E0F80 / 32`; i.e. for L ≥ B the mapping is
  `phys = floor(B/64)*66 + (L − B)`, and for L < B it is `floor(L/64)*66 + L%64`
  (checked: logical 0x4280 → physical 0x4494). FWSC sets B = `floor(cap/66)*64` = whole chip;
  the firmware sets B = `floor((cap−0x10000)/66)*64`, leaving the last 64 KB plain — which is why
  the BT pairing records at physical 0x1F3000 are stored 1:1.
- Since the ROM programs nothing here and a factory part boots, the reset default must leave
  offset 0 inside the CRC area. Most likely `0xc0140000` bit 2 (which only FWSC/firmware sets)
  is what enables the split at all. **INFERRED, not confirmed** — settle it with a cold-state
  register dump (see below).

### (d) acceptance test and the ADFU fall-through — **CONFIRMED**

1. `buf[2]` at virtual 0x02000002: `0xA5` → 512-byte MBREC, `0x5A` → 1024-byte, anything else →
   immediate reject.
2. `0xbfc0064d(buf, len)` — 16-bit checksum:
   `sum = Σ u16[i] for i in 1 .. len/2-1; sum += 0x1234; return (u16[0] == sum) ? 0 : 1`.
   Verified on the factory MBREC: byte 2 = 0x5A, len 1024, computed 0x5186 == stored 0x5186
   (with len 512 it computes 0xecf5 and would fail, so the 0x5A/1024 path is the live one).
3. Accept → `jalx 0xbfc000b4` with `a0 = 0x02000010`, i.e. **jump to MBREC+0x10**.
4. Reject → next of the 24 attempts; after all 24, `0xbfc01699` restores the CMU/pad registers
   saved by 0xbfc015f5 and clears CTL bit 2, the loop returns, and main falls through to card boot
   (0xbfc005d1) and then USB ADFU (0xbfc00501 → `jalx 0xbfc030d0`), which is the 10d6:10d6 device.

### (e) offset and length — **CONFIRMED**

MMU `phy` (= logical NOR offset) **0** on the first outer pass, **0xF840** on the second.
1024 bytes are checksummed (512 if byte 2 is 0xA5). Physically that is lines 0..15,
physical 0x0000–0x041F.

### Exact register writes before the first NOR read, in order

```
once:    [0xbfc20014+8]  = [0xc0001004]      # 0xbfc015f5: save CMU
         [0xbfc20014+56..68] = [0xc00900cc], [0xc00900d0], [0xc00900d4], [0xc00900d8]
         [0xbfc20014+40..52] = [0xc0090020], [0xc0090024], [0xc009002c], [0xc0090030]
         [0xc0001028] = 0x41
         [0xc0001004] |= 0x4000
         [0xc0001064] |= 0x800
per try: [0xc0110000] = 0x00180008                      # 0xbfc01481, after delay(64)
         [0xc00900d0] &= ~0x801 ; [0xc00900d4] &= ~0x801 # 0xbfc010e1(i,1)
         <case i of 6: pad group select via 0xbfc01071,
          [0xc0090020]/[0xc0090024]/[0xc009002c]/[0xc0090030] low nibble = 1,
          [0xc00900cc]/[0xc00900d0]/[0xc00900d4]/[0xc00900d8] = (x & ~0x7010) | 0x5020,
          and [0xc0110000] |= 0x200 (i=0, 3-wire) or &= ~0x201 (i=1, 4-wire), etc.>
         [0xc0110000] |= 0x100 ; poll [0xc0110004] & 0x100 == 0x100   # 0xbfc0152d
         [0xc0110000] |= 0x08
         [0xc0110000] &= ~0x09
         [0xc0110000] = ([0xc0110000] & ~0x04) | 0x02
         [0xc0110008] = 0xFF                            # flash cmd 0xFF, exit continuous read
         <wait tx done, 0xbfc01735>
         [0xc0110000] &= ~0x04
         [0xc0110000] |= 0x08
         [0xc0110000] &= ~0x101
         [0xc0140004] = 1 ; poll ; [0xc0140000] = 1      # 0xbfc014b5
         [0xc00a0090 + 8i] = 1 for i in 0..15
         [0xc00a0090] = 0x42000000 ; [0xc00a0094] = phy
         [0xc012001c] |= 1                               # 0xbfc00fd9
         [0xc0110000] |= 0x1000     (attempt A)  /  &= ~0x1000  (attempt B)
         read 0x02000000..
```

CTL bit meanings, cross-checked against the SDK HAL (`hal_spi.h`, `hal_spi1.h`) and NorFwsc:
0x01/0x02 = transfer direction, 0x04/0x08 = FIFO/CS handling, 0x100 = reset/busy handshake,
**0x200 = 3-wire** (`hal_spi_set_3wire`, NorFwsc 0xbfc2c915 does `CTL |= 0x200`),
**0x400/0x800 = dual/quad line mode** (`hal_spi_set_line`, NorFwsc 0xbfc2c8b1),
**0x1000 = cache randomize / CRC-line read path**, **0x2000 = randomize on the write path**.
What the SDK loaders do *differently* from the ROM: only the boundary block
(SPI0+0x20, 0xc0140008, 0xc014000c, 0xc0140000 bit 2) plus setting CTL 0x2000 alongside 0x1000.
Everything else — CTL = 0x00180008, the 0xFF wake, the cache invalidate — is the same code shape
(NorFwsc 0xbfc2c8b1 uses the identical literal pool: 0xc0110000, 0xc0110004, 0xc012001c,
0x00180008, 0xc0140000, 0xc0140004).

### The chip currently holds exactly the right thing

`tools/norline.py` decodes a raw dump back to logical bytes and re-encodes a logical image to
compare. Against the latest raw read:

```
$ python3 tools/norline.py decode dumps/raw_0x000000_e9418_m0_164007.bin /tmp/dec.bin
14476 lines, 50 erased, 0 bad CRC
$ python3 tools/norline.py verify dumps/raw_0x000000_e9418_m0_164007.bin dumps/nor_logical_1MB.bin
match 14426, MISMATCH 0, erased-in-raw 50
```

The 50 erased physical lines are exactly logical 0x400–0xF80 and 0xE2240–0xE22C0, which are FF in
the factory image too. The decoded image is **byte-identical to `dumps/nor_logical_1MB.bin` over
all of 0x0–0xE2300**: MBREC at 0, BREC at 0xFC0, nor_id at 0x1000, LFI head at 0x4280. Physical
0xEA000–0x1F3000 is erased (bank B blank, second MBREC slot blank) and the BT records at physical
0x1F3000–0x1FEB87 survive.

Timeline recovered from the raw dumps (all of them decode cleanly, which is itself a strong
cross-validation of the codec):

| dump | layout on the chip |
|---|---|
| raw_0x000000_2e00, _40008_m0, raw_ref_sdk_layout | flash #1: SDK layout, **BREC at logical 0x400** → the device MBREC looks at 0xFC0 → no boot |
| _40008_m0_163244, _e9418_m0_163602 | flash #2 mid-way: BREC at 0xFC0 ✓, **MBREC still erased** |
| **_e9418_m0_164007** | flash #2 complete: MBREC + BREC + LFI all correct |

So the "BREC/LFI at the wrong offset" diagnosis in the previous section was right **for flash #1**
and has since been fixed; recovery option (a) evidently worked. **The physical format was never
the problem and is not the problem now.** What the first 1024 bytes must physically contain is
`norline.encode()` of the 1024-byte MBREC, i.e. physical 0x0000–0x041F = 16 CRC lines — and that
is bit-for-bit what is there.

### Does the ROM have a NOR write path? — **No. CONFIRMED**

The ROM's entire SPI0 usage is the register set above. The only write to TXDAT (0xc0110008) is the
constant `0xFF` in 0xbfc0152d; no WREN (0x06), page-program (0x02) or erase (0x20/0x52/0xD8)
opcode is emitted anywhere, and 0xc0110014/+0x18/+0x1c/+0x20/+0x24 are never touched. The ROM
reads NOR only through the MMU window. There is no ROM entry point to call for a raw write.

Two routes remain if a format-independent write is ever needed (both need their own review before
any device use):
- a custom MIPS32 stub run via ADFU CALL_ENTRY driving SPI0 directly. 0xbfc0152d is a complete
  worked example of the controller protocol for sending one command byte (CS via CTL bit 3,
  direction via CTL bits 0–1, byte count at +0x10, data at +0x08, handshake on STATUS bit 0x100);
- NorFwsc's own primitives, which are already proven on this unit: the raw read
  `0xbfc2d065(phys24, buf, len)` and the page program 0xbfc2f7c9 with CTL bit 0x2000 left clear
  (that bit is what applies the scrambler — `dumps/raw_0x000000_420_m2000.bin`, a raw read taken
  with CTL\|0x2000, comes back re-scrambled and CRC-invalid, while the m0, m8000 and ma000
  variants are identical to each other and decode cleanly).

### What to check next about the no-boot

The NOR is exonerated, so the remaining candidates are all in the chip's cold reset state. The
cheapest next step is read-only and the device is already sitting in boot-ROM ADFU, which *is* the
cold state for every register the ROM does not write:

1. **Dump SPI0 +0x14/+0x18/+0x20/+0x24 and 0xc0140000..+0x10 now, with `lw`.** The existing
   `registers_lw.txt` is warm (firmware had run). A fresh dump gives the reset defaults directly
   and answers (c) outright. If SPI0+0x20 / 0xc0140008 read as 0 at cold boot, then the whole chip
   is "plain" by default and the MBREC would have to be stored 1:1 after all — which would
   contradict the MBREC-code argument above, so this is the one measurement that could overturn
   the conclusion. If SPI0+0x14 reads non-zero, there is a key after all and the write needs the
   FWSC header's `spi_decrypt`/efuse-key fields filled in instead of zeroed.
2. The eFuse word returned by 0xbfc006ad: bit 0x2000 makes the ROM run 0xbfc01769 *before* the NOR
   boot. Worth reading 0xc0100000/0xc010000c in the cold state too.
3. The GD25Q16 non-volatile status registers (0x05/0x35/0x15). NorFwsc takes its opcodes from the
   chipinfo structure we supply, so nothing should have changed them, but a read is free.

## Update 2026-10-25 (b): where the boundary comes from, the full register inventory, SPI0+0x18

Driven by the team's measured register states A/B/C. Three results, all static.

### 1. The 64 KB raw tail is a hard-coded constant in BREC — CONFIRMED

`BREC 0x0300110d(a0 = chipinfo)` runs just before the driver probe (called from 0x03001cb2):

```
3001110: lw a0,4(a0)                  ; chipinfo[+4] = capacity in 512-B sectors (0x1000)
3001112: lw v1,[0x30011e4]=0xffff0000 ; literal -0x10000  (NOR logical 0x21a4)
3001114: sll a1,a0,8
3001116: sll a3,a1,1                  ; a3 = sectors<<9 = 0x200000 total bytes
3001118: lw s0,[0x30011e0]=0x9fc21a04
300111a: addu v0,a3,v1                ; *** total - 0x10000 ***
300111c: sw v0,0(s0)                  ; *** 0x9fc21a04 = 0x1F0000 ***
300111e: lbu a0,56(a2) ; sb a0,0(0x9fc21a00)   ; clock/mode = chipinfo[56]
3001142: lw a0,4(a2); sll; sll; sw v0,8(a1)    ; info[8] = FULL total 0x200000
300114a: lbu a0,29(a2); sb a0,2(a1)            ; info[2] = chipinfo[29]  page-program opcode
300114e: lbu s0,30(a2); sb s0,3(a1)            ; info[3] = chipinfo[30]  erase opcode
3001130..40: info[0] = (info[0] & 0xB0) | 0x80
3001182: *(0x9fc21924) = ops+0x18 ; *(0x9fc21928) = ops+0x1c
```

Then the probe `0xbfc39af5` divides **that** value by 66:

`SPI0+0x20 = 0xC0140008 = ((total - 0x10000)/66)*64 = (0x1F0000/66)*64 = 30782*64 = 0x1E0F80`,
`0xC014000C = 0x003FFFC0`, `0xC0140000 |= 4`, `info[0x10] |= 0x40`, `info[0x11] = cap % 66 = 68`.

So the team's measured 0x001E0F80 is **exactly** reproduced, and the reserved raw tail is the
literal `0xffff0000` at BREC XIP 0x30011e4 (= nor_logical_1MB.bin offset **0x21a4**) — it does
**not** come from any BREC/LFI header field. Last complete line ends at physical
30782*66 = 0x1EFFBC; physical 0x1EFFBC..0x1F0000 (68 B) is the `cap % 66` remainder.
Note `info[8]` keeps the **full** 0x200000, so the bounds check in nor_ud 0x1000773d still
admits physical addresses in the raw tail.
`info[0] = (info[0] & 0xB0) | 0x80` is byte-for-byte what SDK NorFwsc 0xbfc2e9e5 does to its
F[0] — the two agree on the caps byte.

### 2. Complete register-write inventory of the driver blob — CONFIRMED

Every `sw`/`sb`/`sh` in the blob, with pool-constant bases resolved *and* base+offset forms
checked (script: scratchpad `re/regwrites.py`):

| block | registers written | count |
|---|---|---|
| SPI0 0xC0110000 | **+0x00 CTL** 55, **+0x08 TXDAT** 21, **+0x10 BC** 3, **+0x20** 1 | 80 |
| NOR/XIP ctl 0xC0140000 | +0x00 (2), +0x04 (1), +0x08 (1), +0x0C (1) | 5 |
| CMU | 0xC0000000 (7), 0xC0000108, 0xC000011C, 0xC0001004, 0xC0001028 (2), 0xC0001064, 0xC0001068 (2) | |
| pin mux | 0xC0090018/1C/20/24/2C/30/3C/40, 0xC00900CC/D0/D4/D8 | |
| other | 0xC012001C (1) | |

**SPI0+0x14, +0x18, +0x1C and +0x24 are never written** — not by the blob, not by BREC, not by
nor_ud, not by NorFwsc, NorHwsc, ADFUS, Fwsc.bin or NorMbrec (checked by literal scan *and* by
offset-from-base scan). Two earlier leads were false positives: the "sh → SPI0+0x22/+0x3A/+0x20"
hits in NorFwsc are its **ops table** (words 0xbfc2cc30, 0xbfc2cd51, …) misparsed as code, and
the old note "init writes hdr chipid to SPI0+0x14 with CTL|0x4000" does not survive: NorFwsc
contains no 0xC0110014 literal at all, and the `&= ~0x4001 / |= 0x4000` pair in the factory
driver is on **CMU 0xC0000000**, not on SPI0 CTL.

**Read vs write differ only in CTL, and neither touches +0x18 or +0x20:**

- read `0xbfc39605`: `CTL &= ~3`; `CTL = (CTL&~3)|2`; `CTL &= ~8`; TXDAT 0x0B + 3 address bytes
  + 1 dummy; then per chunk ≤ 0x8000: `CTL &= ~3`, `BC = n`, `CTL = (CTL&~3)|1`, PIO-read RXDAT
  while `!(STAT & 4)`; finally `CTL |= 8`. **It never touches 0x2000 or 0x8000**, so it returns
  raw physical bytes in whatever engine state it inherits (0x8000 set, i.e. bypassed, after any
  line-mode write).
- write `0xbfc39765`: `CTL &= ~0x8000` per 256-B page chunk, `CTL &= ~0x2000` then `|= 0x2000`
  at every 66-B line boundary, `CTL |= 0x8000` then `CTL |= 8` at the end — **only when
  `info[1] != 0`**.
- So line coding on the **PIO** path is selected per access by CTL 0x2000 (enable) / 0x8000
  (bypass), driven solely by `info[1]`; on the **XIP/MMU** path it is 0xC0140000 bit 2 plus the
  0xC0140008 / 0xC014000C pair. There is no decoding read in this driver at all.

### 3. SPI0+0x18 is the CRC-error address capture — CONFIRMED

The one place in the system that uses it is the **device kernel's line-CRC exception handler**.
It lives in the first 0xF00 bytes of kernel.drv, which is the one region the XIP dump misses
(kernel.drv = NOR 0x4a80..0xbf00; the XIP window starts at NOR 0x5980), so it was invisible
earlier. Disassembled from `nor_logical_1MB.bin[0x4a80:0xbf00]` at vma 0x0FFFF100
(= 0x10000000 − 0xF00):

```
; handler, 0x0ffffb95
ffffb98: lw v0,[0xffffc58]=0xc0110004
ffffb9a: lw v1,0(v0)
ffffb9c: li v0,128
ffffb9e: and v0,v1
ffffba0: beqz v0,0xffffc0d            ; *** SPI0 STATUS bit 0x80 = line CRC error ***
ffffba2: lw v0,[0xffffc5c]=0xc00a0094 ; MMU entry 0 logical word
ffffba8: lw v0,[0xffffc60]=0xc0110018
ffffbaa: lw v0,0(v0)                  ; *** failing address from SPI0+0x18 ***
ffffbb0: jalrc [0x10000dbc]           ; read EPC
ffffbbe: jalrc [0xbfc04919]           ; ROM printf "code CRC ERR:0x%x, EPC:0x%x"
ffffbd0: jalrc [0xbfc04919]           ; "ERR_DATA:0x%x,ERR_CODE:0x%x,0x%x\n" with
                                      ;  0xc00a0110 / 0xc00a0114 (cache miss/hit counters)
; retry helper, 0x0ffff21d(a0 = a word inside the failing line)
ffff228: lw s0,0(a1)                  ; save *(0xc00a0094)
ffff22a: lw a2,0(a3)                  ; a2 = *(0xc0110018)
ffff22e: sw a2,0(a1)                  ; MMU entry 0 logical = failing address
ffff230: jalr [*(0x9fc21100)+0x18](10)
ffff23c: sw a0,0(a1)                  ; *(0xc0110004) = 0x80   <- W1C: clear the CRC error
ffff23e: lw v0,0(s1)                  ; re-read the word (forces a refill)
ffff240: lw a3,0(v1) ; or a3,1 ; sw   ; poke 0xc012001c |= 1
ffff246: lw v0,0(a1) ; and v0,a0
ffff24a: bnez v0,0xffff23d            ; retry while the error re-asserts
ffff252: sw s0,0(a2)                  ; restore MMU entry 0
```

Literal strings in the device image: `"code CRC ERR:0x%x, EPC:0x%x"` at kernel vma 0x0FFFFC18
and `"ERR_DATA:0x%x,ERR_CODE:0x%x,0x%x\n"` at 0x0FFFFC34.

**Therefore:**
- **SPI0+0x04 bit 0x80 = line CRC error, write-1-to-clear.**
- **SPI0+0x18 = the (logical) address captured on a line CRC error.** Hardware loads it; no
  software ever writes it.
- The measured state **C** (`+0x04 = 0x96`, `+0x18 = 0x40`) therefore means a line CRC error was
  **latched and pending** when the device was switched to ADFU, captured at logical 0x40. The
  measured state **B** `+0x18 = 0x00FFFFC0` is likewise hardware-loaded, not a configuration the
  FWSC wrote.
- **SPI0+0x20** is used the same way by nor_ud `0x10007a61` and `0x10007bdb`: guarded by
  `info[0x10] & 0x40` (line mode), they save `*(0xc00a0094)`, set it to `*(0xc0110020)`, do the
  access, and restore — i.e. the logical offset to park in MMU entry 0. BREC `0x030004cd` /
  `0x030005b1` write it with `(base[active LFI]/66)*64` (= 0x4280 for bank A), and the probe and
  FWSC init write it with the logical capacity. Dual-purpose; exact hardware semantics UNKNOWN.
  Either way **no read or write path of the driver consults it**, so it is not a mode selector.

### Why state B reads raw through the MMU — two candidates, both testable

In state B the whole 0xC014 block is already correct (`0xC0140000 = 5`, so bit 2 *is* set,
`+8 = 0x1E0F80`, `+0xC = 0x003FFFC0`), so bit 2 alone is not what is missing. The remaining
differences from state C are:
1. **SPI0 CTL = 0 in B vs 0x38 in C.** 0x38 = bits 3,4,5; bit 3 (0x8) is what every driver access
   leaves set, and the blob's own bring-up ends with roughly `(div<<16) | 0x68` before the access
   bits are toggled. With CTL = 0 the controller has no divider and no CS idle state. Cheap test:
   write CTL = 0x38 in state B and repeat the MMU read. INFERRED.
2. **The MMU entry's second word is a logical offset** — a fact you only just established. If the
   state-B experiment programmed a *physical* offset there, raw-looking bytes are the expected
   result and nothing is broken. This is the simpler explanation; rule it out first.

Note that neither 0x2000 nor 0x8000 is set in state C, confirming those two bits gate only the
PIO path, not the XIP decode.

### Correction to update (a): the feedback window is ≥ 3 bytes, not proven to be exactly 3

Collision statistics over the 16 MBREC line pairs (1024 samples), counting repeated keys and
keys whose mask disagrees:

| history | distinct keys | repeat events | keys with disagreement |
|---|---|---|---|
| stored 1 B | 250 | 774 | 222 |
| stored 2 B | 866 | 158 | **2** |
| stored 3 B | 873 | 151 | **0** |
| stored 4 B | 876 | 148 | 0 |
| plain 1 B | 189 | 835 | 122 |
| plain 2 B | 605 | 419 | 108 |
| plain 3 B | 731 | 293 | **71** |
| plain 4 B | 797 | 227 | 48 |

- The feedback is **definitively from the stored stream, not the plaintext**: 3 bytes of plaintext
  history still leaves 71 disagreeing keys over 293 repeat events, while 3 bytes of stored history
  leaves none over 151.
- 2 bytes of stored history already predicts 156 of 158 repeat events, so the dependence is
  dominated by the last two stored bytes with a small correction from older ones. 3 bytes explains
  everything observable, but the data cannot distinguish 3 from a longer window (random 24-bit keys
  would collide only ~0.02 times by chance at this sample size), so "exactly 3" is **not** proven.
- No compact algebraic form: fitting each output bit of `F` as an ANF over the 24 history bits is
  inconsistent at degree 1 (3471 bad equations of 873×8) and degree 2 (301 features, 2255 bad), in
  both bit orders. A bit-serial model with a fixed 24-bit window fails at degree 1, 2 and 3. So
  there is no cheap software encoder; **UNKNOWN** and probably an S-box.
- This does not weaken the operational conclusion: the decoder is `plain[k] = stored[k] ^
  F(recent stored bytes)`, the exact inverse of the encoder, so anything written through the
  engine reads back correctly and software never needs `F`.

### Concrete ADFU recipe for the factory driver (supersedes the sketch in update (a))

Chipinfo found: **BREC+0x40 = NOR logical 0x1000**, 64 bytes, id `c8 40 15` (GD25Q16, matches the
HWSC-reported flash id):

```
c8 40 15 ff 00 10 00 00 80 00 20 00 40 00 40 00 08 00 00 02 00 00 00 00 00 00 00 00 01 02 d8 00
05 03 7c 40 00 40 00 10 04 40 80 0f 08 40 00 0f 14 40 00 08 00 00 00 00 03 78 00 00 00 00 00 00
```
+4 = 0x1000 sectors (0x200000 B), +28 = 0x01, +29 = **0x02** (page program), +30 = **0xD8** (64 KB
block erase), +31 = 0x00, +32 = 0x05 (bus mode), +56 = **0x03** (clock/mode).

**BREC+180 = 0x00**, so `0xbfc39999` (pin mux) returns immediately on this unit — no BREC image
needs to be mapped at 0x03000000, and the whole pin-mux path can be skipped. That removes the
only dependency on BREC code.

1. Upload `nor_logical_1MB.bin[0x2f3c:0x3d4c]` (3600 B) to **0xbfc39200** exactly.
2. Zero 0x9fc21800..0x9fc21a64 (0x264 B), then set:

| address | value | meaning |
|---|---|---|
| 0x9fc21800 | 0x80 | info[0] caps, exactly what BREC computes from a zeroed struct |
| 0x9fc21801 | 66 / 0 | **line length — set before every write** |
| 0x9fc21802 | 0x02 | page-program opcode (chipinfo[29]) |
| 0x9fc21803 | 0xD8 / 0x20 | erase opcode: 0xD8 = 64 KB, 0x20 = 4 KB (chipinfo[30]) |
| 0x9fc21808 (u32) | 0x00200000 | capacity for the bounds check — the **full** chip |
| 0x9fc21810 | 0x01 | bus mode bit (probe ORs in 0x40) |
| 0x9fc21a00 | 0x03 | clock/mode (chipinfo[56]) |
| 0x9fc21a04 (u32) | 0x001F0000 | **total − 0x10000; this is what produces the 0x1E0F80 boundary** |
| 0x9fc21924 (u32) | 0xbfc39870 | irq_off, in case anything uses that table |
| 0x9fc21928 (u32) | 0xbfc3987c | irq_restore |

3. Call `0xbfc39a95` with a0 = 1 (CMU: 0xC0001004 |= 0x4001, 0xC0000000 &= ~2 then |= 1,
   0xC0001068 &= ~4).
4. Call `0xbfc39af5` with a0 = 0, a1 = 7. This runs the 6-variant clock/mode scan with RDID
   (0x9F, fallback 0x90) and on success programs `SPI0+0x20 = 0xC0140008 = 0x1E0F80`,
   `0xC014000C = 0x003FFFC0`, `0xC0140000 |= 4`, `info[0x10] |= 0x40`, `info[0x11] = 68`.
   It returns the 4-byte JEDEC id in `v0`/at 16(sp) — expect `c8 40 15` as the sanity check that
   the whole bring-up worked. **If this returns something else, stop.**
5. Then, per operation (all MIPS16 — call `addr|1`):
   - `erase(a0 = physical byte address)` = `0xbfc39575`, opcode from `info[3]`.
   - `write(a0 = physical byte address, a1 = buf, a2 = physical length)` = `0xbfc39765`. For line
     regions set `info[1] = 66` and give a **66-aligned** address and a length that is a multiple
     of 66, with 64 data bytes + 2 placeholder bytes per line (hardware fills the check word). For
     the raw tail set `info[1] = 0` and pass plain bytes at any alignment.
   - `read(a0 = physical, a1 = buf, a2 = len)` = `0xbfc39605`, always raw physical bytes — useful
     as a verification read-back, remembering it returns the encoded form.
   - Wrap in `0xbfc39870` / `0xbfc3987c` only if interrupts can fire; under ROM ADFU they cannot.
6. Nothing else needs faking: the blob brings up its own CMU clocks, SPI0 and XIP cache, and needs
   only the ROM slots `*(0xbfc002bc)` and `0xbfc0421c`, which the boot ROM always provides.
   0xbfc39200..0xbfc3a010 is clear of the ADFUS stub (0xbfc28000), its buffers
   (0xbfc29000..0xbfc2bfff) and the ROM stack (sp = 0xbfc45ff0).

Still the cheaper route: patch NorFwsc's three `+1024` immediates to 0xfc0 (recovery option (a)).
The factory driver is a fallback if that fails, or a verification tool — step 5's raw `read` gives
a true physical read-back without needing the MMU, which the SDK loaders cannot do at all.

### Addendum: CTL bit 14 (0x4000) and the randomizer seed (2026-10-25)

**The boot ROM leaves CTL bit 14 clear and never writes the seed register. CONFIRMED.**

Exhaustive, not sampled: `0xc0110014` appears in no literal pool in the 32 KB ROM, and the ROM's
only MIPS32 `lui` of a peripheral base is `bfc030dc: lui t8,0xc00b`, so there is no other
addressing route. Every immediate in the ROM whose value has bit 0x4000 set:

| addr | value | goes to |
|---|---|---|
| 0xbfc014ba | 0x4000 | argument of the delay loop 0xbfc003f5 |
| 0xbfc0164c | 0x4000 | `[0xc0001004] \|= 0x4000` (CMU) |
| 0xbfc031cc / 0xbfc031d4 | 0x4000 | `[0xc0090034] = 0x4000`, `[0xc0090038] = 0x4000` (pads, USB-ADFU path) |
| 0xbfc01160…0xbfc01448 | 0x7010 / 0x5020 / 0x5022 / 0x5026 / 0x5029 | pad regs 0xc00900cc/d0/d4/d8 |
| 0xbfc005ac | 0xf840 | the second MBREC logical offset |
| 0xbfc00914, 0xbfc01e7e, 0xbfc01edc, 0xbfc01f92, 0xbfc01fa2, 0xbfc02976, 0xbfc02f46, 0xbfc02fb0 | — | card / USB paths |

All 25 references to a SPI0-CTL literal (0xbfc01068, 0xbfc01460 ×12, 0xbfc014a8, 0xbfc015e8 ×7,
0xbfc01708, 0xbfc03084) are accounted for, and the only values they apply are
0x00180008 (absolute store), 0x200, ~0x201, 0x100, 0x08, ~0x09, ~0x04|0x02, ~0x101, 0x1000,
~0x1000, ~0x04 and 0.

**CTL value the ROM ends up with for the MBREC read:** `0x0018120A` on attempts i = 0, 2, 4
(3-wire, `CTL |= 0x200`) and `0x0018100A` on i = 1, 3, 5 (`CTL &= ~0x201`).
Bit 20 (0x100000) and bit 19 come from the base constant 0x00180008 stored at 0xbfc01481
(literal 0xbfc014ac); bit 12 (0x1000) is set at 0xbfc01002; **bit 14 is never set**.
Trace: `0x00180008` → `|0x200` → `|0x100` → `|0x08` → `&~0x09` → `(&~0x04)|0x02` → `&~0x04`
→ `|0x08` → `&~0x101` → `|0x1000`.

**Correction to the register captures (this was inverted in the working notes).** The set
"CTL = 0x38, STATUS = 0x96, +0x18 = 0x40, +0x20 = 0x80" is the **byte-wise-read artifact**
(`dumps/extract/registers.txt`, taken through the ROM's byte-wise 0x93 command, which only
returns the low byte and garbles even that). The `lw` read of the *same* session is
`dumps/extract2/registers_lw.txt`: **CTL = 0x00361838, STATUS = 0x14, +0x14 = 0,
+0x18 = 0x00FFFFC0, +0x20 = 0x001E0F80**. So in the state where the hardware line/CRC decode
demonstrably worked on factory-written data, bit 12 and bit 20 were set, **bit 14 was clear**, and
the seed register read 0 — which matches the ROM exactly.

**Where NorFwsc sets bit 14, and why the stock writer does not.**

NorFwsc writer entry 0xbfc2fa95, at 0xbfc2fab2–0xbfc2fad0 (a1 = 0xbfc2fb9c → F0 = 0xbfc2fc60,
a3 = 0xbfc2fba0 → 0xc0110000):

```
bfc2fab2  v0 = *(u8*)0xbfc2fc60          # F0
bfc2fab4  v1 = 128 ; neg v1              # ~0x80
bfc2fab8  v0 &= ~0x80
bfc2faba  beqz v0, 0xbfc2fad2            # skip unless F0 has a bit other than 7 set
bfc2fabe  CTL |= 0x2080                  # bits 13 and 7
bfc2faca  CTL |= 0x6000                  # bits 13 and **14**
```

The stock BREC NOR driver (the same library; NOR 0x2f3c → RAM 0xbfc39200; it wrote the factory
image) has **no counterpart**. Its writer entry 0xbfc39765 does, at 0xbfc3979e–0xbfc397ac:

```
bfc3979e  if (*(u8*)(0xbfc39860+1) == 0) skip   # line length == 0 -> plain area
bfc397a2  CTL &= ~0x8001
```

— a *clear*, and nothing else; it relies on bit 13 already being set by
`hal_spi_phy_init(cache_randomize = true)` (`CTL |= 0x3001; CTL &= ~0x8001`, NorFwsc 0xbfc2c84a).
The per-66-byte-line strobe is byte-identical in both and must be left alone:
`CTL &= ~0x2001; CTL |= 0x4001` at NorFwsc 0xbfc2f834 / 0xbfc2f840 and stock 0xbfc397ec /
0xbfc397fa. Neither driver ever writes 0xc0110014; there is no such literal in either, so the seed
register stays at its reset value.

So the only difference between the two writers' CTL setup is the 0xbfc2fabc–0xbfc2fad0 preamble
(bits 7, 13, 14 asserted for the whole write). Caveat worth stating: because the stock strobe also
ORs 0x4000, bit 14 cannot be *only* "software-seed select" — or the factory image would be
seed-scrambled too and the ROM could not have read it. The safe conclusion is that this preamble is
what differs, not that bit 14 alone is the seed select.

**One-byte patch to make NorFwsc's writer match the stock driver** (file offsets in
`build/eats2819/afi_base/NorFwsc.bin`, load base 0xbfc2c000):

```
offset 0x3abb:  0x22 -> 0x10      # halfword 0x3aba: 220b (beqz v0,0xbfc2fad2) -> 100b (b 0xbfc2fad2)
```

That skips the preamble entirely. After `hal_spi_phy_init(randomize)` the state is already
bit 0 clear, bit 13 set, bit 12 set, bit 15 clear — i.e. exactly what the stock driver's
`CTL &= ~0x8001` preamble produces. Verified by disassembling the patched copy:
`bfc2faba: 100b  b 0xbfc2fad2`.

Equivalent two-halfword alternative, if keeping the code shape is preferred (both sites then
become `CTL |= 0x2000`, a no-op since bit 13 is already set):

```
offset 0x3abe:  0x84 0xf0 -> 0x04 0xf0   # addiu v1,8320 (0x2080) -> addiu v1,8192 (0x2000)
offset 0x3aca:  0x0c 0xf0 -> 0x04 0xf0   # addiu v1,24576 (0x6000) -> addiu v1,8192 (0x2000)
```

Do **not** patch 0xbfc2f840 (offset 0x3840, `addiu v1,16385`) — the stock driver has the identical
instruction at 0xbfc397fa.
