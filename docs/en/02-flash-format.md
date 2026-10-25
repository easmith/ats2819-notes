# The chip, and how data physically sits in flash

The single most important thing in this repository: **the firmware as an image and the content of
the flash chip are not the same bytes.** [Русская версия](../ru/02-flash-format.md).

## The platform

The headset is built on an Actions **ATS2819** SoC, of the **US281B** family. Identified by matching
its configuration identifiers (`FACTORY_FW_INFO_*`) against the public SDK
[github.com/Pachouli/2819p](https://github.com/Pachouli/2819p), and confirmed by disassembling the
boot ROM and the device's own loaders.

The core is **MIPS** — a mix of MIPS32 and MIPS16, switching instruction sets. The boot ROM is
signed `UCOS @Actions`. SDK loaders and object files are mostly MIPS16:

```bash
/opt/homebrew/opt/binutils/bin/gobjdump -d -m mips:isa32 file.o     # handles MIPS16
```

Capstone does **not** support MIPS16, only MIPS32. Expect to use `gobjdump`.

## Address map

MIPS addressing: `0x8xxxxxxx` is cached (kseg0), `0xaxxxxxxx`/`0xbxxxxxxx` uncached (kseg1); the
same physical address is visible through both.

| Address | What it is |
|---|---|
| `0x10000000` | The flash mapping window (XIP) — the firmware executes and is read through it |
| `0x02000000` | The window the ROM itself sets up (mapping entry 0) |
| `0xbfc00000` | Boot ROM (~32 KB) plus on-chip RAM |
| `0xbfc28000` | Where the vendor tool loads the ADFUS loader |
| `0xbfc2c000` | Where HWSC and FWSC are loaded (they share the slot, run in turn) |
| `0xbfc040ed` | The ROM's ADFU command loop entry (MIPS16) |
| `0xbfc45ff0` | Top of the ROM stack |
| `0x9fc3a000` | Where the firmware loads `upgrade.bin` during a disk upgrade |
| `0xc0000000` | RMU, module resets (bit 6 = SPI0, bit 14 = SPI cache) |
| `0xc0001028` | `CMU_SPICLK` |
| `0xc0001064` | `CMU_MEMCLKEN`, including the SPI cache RAM |
| `0xc00a0000` | Flash mapping controller (MMU), 16 entries |
| `0xc0110000` | SPI0 controller registers |
| `0xc0120000` | Watchdog (`+0x1C`) and friends |
| `0xc0140000` | SPI cache controller: enable, reset, CRC-region bounds |

## The flash chip

**GD25Q16** (GigaDevice), JEDEC id `C8 40 15`, **2 MB**, on SPI0. Reads with `0x0B`/`0x3B`/`0x6B`
(fast read), block erase `0xD8`, sector erase `0x20`, page program `0x02`. Identified on the device
by the HWSC loader, matching both the device's own BREC and the SDK's `nor_id.bin` table.

## Two regions, two storage formats

| Physical range | How it is stored | What lives there |
|---|---|---|
| `0x000000` … `0x1F0000` | in lines: 64 data bytes + 2 CRC bytes = **66 bytes** | the firmware |
| `0x1F0000` … `0x200000` | **as-is, 1:1**, no lines, no transform | the raw tail: the pairing store lives here |

So the logical capacity of the firmware region is `(0x1F0000 / 66) × 64 = 30782 × 64 = 0x1E0F80`,
and addresses convert as `physical = (logical / 64) × 66 + (logical mod 64)`.

The size of the reserved tail is not a header field but a **constant in BREC**: the literal
`0xFFFF0000` (capacity minus `0x10000`) at offset `0x21A4` of the logical image. The last full line
ends at `0x1EFFBC`; the remaining 68 bytes are unused.

Two independent confirmations:

1. **From data.** Pairing records in the tail region appear in a raw read as **plain text**, and the
   difference between physical and logical addresses there is constant at `0xF07C = 0x1E0F80 / 32`.
   Do not confuse this physical tail with the logical `0xF0000` bound that limits a firmware image:
   the tail is where the pairing store is *stored*, while `0xF0000` is where the device's own
   settings region (VRAM, `spi_stg_cap` in BREC) begins in logical space. An image must stay below
   `0xF0000`; the pairing store sits far beyond it and is untouched by a firmware write.
2. **From registers.** After FWSC init, `SPI0+0x20` and `0xC0140008` both hold exactly `0x001E0F80`.

### The two CRC bytes

Computed **in software** (in the loader, at `0xbfc2f6a1`): CRC16, polynomial `0x1021`, initial value
`0xFFFF`, data bits fed LSB-first, result byte-swapped, stored little-endian. A line of 64 zero bytes
gives `0xDAD6`.

They are checked **by hardware, and only on the cache path (XIP)** — the datasheet says so outright:
"Support 16bit CRC only when Cache access fifo". On a mismatch the cache returns `0xFF` for the whole
line and raises an error flag. An erased region therefore *always* raises a CRC error when read
through the cache (`FF FF` matches no CRC). That is normal and not a sign of damage.

### The randomizer and its seed — the trap

The 64 data bytes of every line pass through a **hardware randomizer** in the SPI0 controller. The
transform is self-inverse, depends only on the line's content, and resets on each line:

```
stored[k] = plain[k] XOR F(stored[k-1], stored[k-2], stored[k-3])
```

`F` is non-linear — no linear scrambler up to 64 taps fits, in any bit order — and its table is in
silicon. The initial state comes from a **seed**, and this is where it comes from:

* `CTL` bit 14 (`RAND_SEL`) **set** → the seed is taken from register `SPI0+0x14` (software);
* bit 14 **clear** → the chip's **built-in eFuse seed** is used.

**The seed is the chip's CHIPID.** The vendor's build script fills a `chipid_data` table (36 words at
offset `+240` of the FWSC header) with random values as camouflage and hides the real
`CHIPID | package_id` at index `chipid_data[35] % 35`. `NorFwsc`'s init
(`0xbfc2c2b8`…`0xbfc2c36c`) does:

```
t8 = 0xC0110000
CTL |= 0x4000                       ; take the seed from the register
[0xC0110014] = header[240 + idx*4]  ; idx = header[380] % 35, with a read-back check
```

**The boot ROM reads flash with bit 14 clear and never writes `SPI0+0x14`** — that is, with the
built-in seed. Its `CTL` while reading MBREC is `0x0018120A` (`CRC_EN` set, `RAND_RXEN` set, bit 14
clear). This was established by enumerating all 25 accesses to `CTL` in the entire ROM.

**How this breaks a device.** In the shipped `NorFwsc.bin` the table at `+240` is all zeros. The
loader therefore writes the firmware scrambled with seed **0**, the ROM later unscrambles it with
the eFuse seed, sees noise, finds no MBREC and goes to ADFU. That is exactly how this headset was
bricked on 25 October 2026.

**The fix** is one word in the uploaded copy of the loader:

```
0xBFC2C308 (offset 0x308 in NorFwsc.bin):  0x34794000  ->  0x34790000
            ori t9,v1,0x4000               ori t9,v1,0
```

Implemented as `use_hw_seed()` in `tools/adfu_flash.py` and applied **by default** (disable with
`--sw-seed`, which is the mode that bricks the device). The seed-register store and its read-back
check are deliberately left in place, so init still returns status 0.

There is a **second** site that touches bit 14 in the write path — `0xbfc2faca` (`CTL |= 0x6000`) —
but it is guarded by a flag check at `0xbfc2fc60` that is false here. Worth remembering if you edit
that path.

> The earlier conclusion that "this unit has encryption off, `decrypt_mode = 0` in BREC, no key
> needed" was **wrong**, and it is what caused the brick. `decrypt_mode` belongs to a different
> mechanism. The hardware randomizer is always on, and its seed is the CHIPID.

## SPI0 registers (`0xC0110000`)

| Offset | Purpose |
|---|---|
| `+0x00` | `CTL` |
| `+0x04` | `STATUS` |
| `+0x08` | `TXDAT` |
| `+0x0C` | `RXDAT` |
| `+0x10` | `BC`, byte counter |
| `+0x14` | `SEED`, the 32-bit randomizer seed |
| `+0x18` | logical address of the line where a CRC error was latched |
| `+0x1C` | `CACHE_MODE` |
| `+0x20` | logical end of the CRC region (`0x001E0F80` here) |

`CTL` bits:

| Bit | Name | Meaning |
|---|---|---|
| 29 | `NO_CRC_RAND_RXEN` | read randomizer for the raw region |
| 21 | `CRC_IRQ_EN` | interrupt on CRC error |
| 20 | `CRC_EN` | check line CRCs |
| 16–19 | `SPI_DELAY` | delay chain |
| 15 | `RAND_PAUSE` | pause the randomizer |
| 14 | `RAND_SEL` | take the seed from `+0x14` instead of the eFuse |
| 13 | `RAND_TXEN` | write randomizer |
| 12 | `RAND_RXEN` | read randomizer |
| 8 | `SPI_REQ` | the CPU takes the FIFO away from the cache |
| 5, 4 | FIFO | enable transmit / receive FIFO |
| 3 | `SS` | chip select |

`STATUS` bits: 8 `SPI_READY`, **7 cache CRC error** (cleared by writing `0x80`), 1 `CRC_ERROR`.

Cache controller at `0xC0140000`: bit 0 enable, **bit 2 raw/bypass mode** (FWSC init sets it, which
is why the register reads `1` cold and `5` after init). `0xC0140004 = 1` resets the cache and the bit
clears itself — wait for it. `0xC0140008` is the logical size of the CRC region, `0xC014000C` the
constant `0x003FFFC0`.

## Execute-in-place and the mapping entries

Flash is not copied into RAM; it is **mapped** through the cache controller at `0xc00a0000`, which
has **16 entries**, each a pair of 32-bit words:

* the mapping word at `0xc00a0090 + 8·i`: `0x40000000 | virtual base address`;
* the second word at `0xc00a0094 + 8·i`: the **logical** offset into the firmware, not the physical
  one.

That the second word is logical is proven: on the working firmware, entry 1 held `0x5980`, and the
content of the `0x10000000` window begins exactly at offset `0x5980` of the logical image. The
hardware does the address conversion and the line decoding.

**The boot ROM occupies entry 0** and maps it to virtual `0x02000000`. Use **entry 0** for your own
reads rather than an apparently free entry such as 9 — otherwise two mappings claim one virtual
address and you read something other than what you meant. That cost time here.

### Reading flash correctly, with decoding

This sequence is taken from `hal_spi_phy_init` in the SDK
(`vendor/2819p/psp_rel/HAL/lib/hal_spi_phy_init.o`) and is implemented as
`tools/adfu_flash.py --xip2`:

```c
[0xC0001064] = 0x001FFFFF;            // CMU_MEMCLKEN, cache RAM included
[0xC0001028] = 0x40;                  // CMU_SPICLK
[0xC0000000] &= ~0x40;  |= 0x40;      // reset SPI0
[0xC0000000] &= ~0x4000; |= 0x4000;   // reset the SPI cache
CTL |= 0x100;                         // SPI_REQ: CPU takes the FIFO
while (!(STATUS & 0x100)) [0xC012001C] |= 1;   // wait for SPI_READY, keep the watchdog quiet
CTL = 0x00180008;                     // base: CRC_EN | delay 8 | SS
[0xC0140000] |= 1;                    // enable the cache
[0xC0140004] = 1; while ([0xC0140004] & 1);    // reset the cache
CTL &= ~0x20; |= 0x20; &= ~0x10; |= 0x10;      // restart the FIFOs
CTL &= ~0x2000;                       // RAND_TXEN off
CTL |= 0x1000; CTL &= ~0x8000;        // RAND_RXEN on, unpause
CTL &= ~0x100; while (STATUS & 0x100);         // hand the FIFO back to the cache
```

Two steps are not in the SDK function, and nothing reads without them:

* **clear bit 2 of `0xC0140000`** — otherwise the cache bypasses decoding and returns raw bytes;
* the **`SPI_REQ` handshake** is mandatory: while the CPU holds the FIFO the cache physically cannot
  read flash, and the window returns nothing but `FF`.

## HWSC and FWSC

Two small programs from the SDK that the vendor tool — and `upgrade.bin` — load into RAM when
flashing:

* **HWSC**, "hardware scan": interrogates the SPI flash and returns the chip's JEDEC id.
* **FWSC**, "firmware scan" and write driver: initializes, checks the chip, then writes the boot
  records and the firmware.

Both load at the **same address `0xbfc2c000`** and run in turn. HWSC is called at offset 0; FWSC's
init is at `+0x200` and its write driver at `+0x400`.

FWSC's init also works out the geometry: `lines = capacity / 66`, then
`SPI0+0x20 = 0xC0140008 = lines × 64`, `0xC014000C = 0x003FFFC0`, `0xC0140000 |= 4`.

**The dangerous parts:**

* the very first call of FWSC's write driver, **in any mode**, erases the first 64 KB of flash —
  where the boot records live. Until you are ready to write, call init only;
* the `NorFwsc.bin` decoded out of `AFI.bin` erases `0x0000`–`0xFFFF` on that first driver call, and
  if `header[2] == 2` it erases the whole chip at init;
* the SDK loader writes BREC at logical `0x400`, while **this** device's MBREC looks for it at
  `0xFC0` — see `fix_brec_offset()` in `tools/adfu_flash.py`.
