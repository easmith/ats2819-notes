# Flashing, and the acceptance read

[Русская версия](../ru/06-flashing.md). Read [../../SAFETY.md](../../SAFETY.md) first.

Everything below runs over ADFU, in one process, with a read-back before the cable comes out. The
seed fix (`use_hw_seed()`) is applied automatically; without it the write produces a device that
does not boot.

## The three steps

```bash
cd /path/to/ats2819-notes

# 1. dry run: ADFUS + HWSC + FWSC init, no write at all
sudo .venv/bin/python tools/adfu_flash.py stock
#    expect: flash id c84015, FWSC init status 0

# 2. write, and verify inside the same USB session
echo WRITE | sudo .venv/bin/python tools/adfu_flash.py ours --write --verify 0x5000
#    expect: BREC ok, LFI ok, MBREC ok, ~8 s, then 20480/20480 IDENTICAL

# 3. unplug, replug, power on
```

`stock` writes the factory image from `firmware/stock/nor_logical_1MB.bin`; `ours` writes
`firmware/prompts-ru-en/afi_plain.bin`, or a freshly built `build/fw/afi_plain.bin` if you made one.
The confirmation on stdin is deliberate: a write needs the literal word `WRITE`.

One line in the output says the seed fix went in. If it is missing, stop:

```
NorFwsc patched at 0xbfc2c308: RAND_SEL not set, write uses the eFuse seed
```

## The acceptance read is the whole point

The only honest check is to read the flash **the way the processor reads it** — through the cache,
with the randomizer running off the chip's own eFuse seed — and compare it with the image. That is
`--verify` during a write, and `--xip2` as a standalone read-only command:

```bash
sudo .venv/bin/python tools/adfu_flash.py stock --xip2 0 0x5000
```

The criterion is one line:

```
matches factory image: 20480/20480  -- IDENTICAL
```

If it matches, the boot ROM will read the same bytes, because it is the same path. If it does not
match, **do not replug and hope** — work out why first.

`--seed 0` forces a read with software seed 0. An image that reads correctly with `--seed 0` but
not without it was written the old, broken way: that is the signature of the bug described in
[07-recovery.md](07-recovery.md).

### Reading the output honestly

* **A cache CRC error flag during the acceptance read is normal.** The gap at `0x400`–`0xFC0` is
  erased, its check bytes are `FF FF`, and that matches no CRC. Judge by the byte comparison, never
  by the flag.
* **Verifying against your own model of the format proves nothing.** `tools/verify_raw.py` builds a
  line dictionary from a previous dump of ours, so it reported "14 476 lines, 0 mismatches" on an
  image the device could not boot. It is good for checking that a write is *repeatable*, not that it
  is *correct*. The same goes for `tools/norline.py`.
* **Only one process, one session.** Do not take the read in a second invocation; see
  [05-adfu.md](05-adfu.md).

## What a successful run looked like

The flash that put Russian prompts on the device:

```
echo WRITE | .venv/bin/python tools/adfu_flash.py ours --write --verify 0x5000
  ADFUS ok, flash c84015, FWSC init status 0
  write 8.4 s
  20480/20480 IDENTICAL    VERDICT PASS
```

Then replug, power on, and the headset speaks Russian. The image it wrote has an LFI of `0xE9900`
bytes ending at logical `0xEDB80` — the limit is `0xF0000`, so about 9 KB spare — and is 47 424
bytes larger than stock, all of it `TTS_RC.LIB`.

## The disk-upgrade alternative

Copy `UPGRADE.HEX` to the root of the device's own `AGP-S22` disk and power-cycle: the firmware
updates itself. It needs no service mode, no `sudo` and no cable games — and it offers **no check
before the write** and runs the FWSC loader carried inside the package. It is not the safer path.
See [04-fwu-format.md](04-fwu-format.md) for what a package needs before it can be trusted.
