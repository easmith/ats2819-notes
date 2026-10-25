#!/usr/bin/env python3
"""Check the NorFwsc loader before it is sent to the device, and say so loudly if it is not ours.

Two edits are applied to the SDK's NorFwsc before it is allowed to write this device's flash:

  * at 0xBFC2C308, `ori t9,v1,0x4000` becomes `ori t9,v1,0` — clearing RAND_SEL, so the write
    engine scrambles with the chip's own eFuse seed instead of a software seed of 0. Without this
    the flash content is correct and CRC-valid and the boot ROM still cannot read it. That is
    what bricked this device once.
  * at 0xBFC2F534/556/5D6, the EXTEND'ed `addiu rx, 1024` immediates become 0xFC0 — the SDK writes
    BREC at logical 0x400, this device's MBREC looks for it at 0xFC0.

Both edits are made by address. If the loader in vendor/afi is a different revision, those
addresses hold different instructions and patching them is how you brick a headset. This module
does not refuse — the operator may have good reason to continue, and a hard stop on a repository
that cannot know every SDK build is its own kind of failure — it prints a warning that is hard to
miss and leaves the decision where it belongs.
"""
import hashlib
import struct
import sys
from dataclasses import dataclass, field

LOADER_ADDR = 0xBFC2C000         # HWSC and FWSC share this slot
SEED_SEL_SITE = 0xBFC2C308       # ori t9,v1,0x4000 in NorFwsc init: CTL |= RAND_SEL
SEED_SEL_WORD = 0x34794000       # the instruction expected there
BREC_OFF_SITES = (0xBFC2F534, 0xBFC2F556, 0xBFC2F5D6)   # EXTEND'ed addiu rx, 1024

KNOWN_NORFWSC_SIZE = 15280
KNOWN_NORFWSC_SHA256 = "3f19af521e787dec798715ade45eb7dea7ae318f29776b9242acb47f9a669e0b"

DOC = "docs/en/07-recovery.md"


@dataclass
class LoaderVerdict:
    sha256: str
    known: bool
    seed_site_ok: bool
    brec_sites_ok: tuple
    problems: list = field(default_factory=list)


def check_norfwsc(data: bytes, known_sha: str = KNOWN_NORFWSC_SHA256) -> LoaderVerdict:
    """Compare a NorFwsc image against the revision these tools were reverse-engineered on."""
    digest = hashlib.sha256(data).hexdigest()
    problems = []

    known = digest == known_sha and len(data) == KNOWN_NORFWSC_SIZE
    if not known:
        problems.append(
            f"this is not the NorFwsc these tools know: {len(data)} bytes, sha256 {digest[:16]}…; "
            f"expected {KNOWN_NORFWSC_SIZE} bytes, sha256 {known_sha[:16]}…"
        )

    seed_ok = False
    o = SEED_SEL_SITE - LOADER_ADDR
    if len(data) >= o + 4:
        seed_ok = struct.unpack_from("<I", data, o)[0] == SEED_SEL_WORD
    if not seed_ok:
        problems.append(
            f"no RAND_SEL instruction at {SEED_SEL_SITE:#x} — the seed patch cannot be applied, and "
            "a write with the wrong seed produces a flash the boot ROM cannot read"
        )

    brec_ok = []
    for va in BREC_OFF_SITES:
        o = va - LOADER_ADDR
        ok = (len(data) >= o + 4 and data[o:o + 2] == b"\x00\xf4" and data[o + 3] in (0x48, 0x49))
        brec_ok.append(ok)
        if not ok:
            problems.append(f"no BREC offset immediate at {va:#x} — BREC would be written at the "
                            "SDK's 0x400 instead of this device's 0xFC0")

    return LoaderVerdict(digest, known, seed_ok, tuple(brec_ok), problems)


def _box(lines, stream):
    width = max(len(line) for line in lines)
    bar = "!" * (width + 4)
    print(bar, file=stream)
    for line in lines:
        print(f"! {line.ljust(width)} !", file=stream)
    print(bar, file=stream)


def warn(verdict: LoaderVerdict, stream=sys.stderr) -> None:
    """Print a hard-to-miss warning if anything about the loader is unexpected. Never raises."""
    if not verdict.problems:
        return
    lines = ["WARNING: unexpected NorFwsc loader — writing may brick the device"]
    for problem in verdict.problems:
        for chunk in _wrap(problem, 96):
            lines.append("  " + chunk)
    lines.append("")
    lines.append(f"Patches that do not match are skipped, not forced. Read {DOC} before writing,")
    lines.append("and keep a full backup: a device stuck in ADFU (10d6:10d6) is recoverable.")
    _box(lines, stream)


def warn_sw_seed(stream=sys.stderr) -> None:
    """Print the warning for --sw-seed, the mode that bricked this device. Never raises."""
    _box([
        "WARNING: --sw-seed will brick the device",
        "  It writes the flash scrambled with software seed 0 while the boot ROM reads with",
        "  the chip's eFuse seed, so the image verifies on the host and the device will not",
        f"  boot. This mode exists only to reproduce that failure. See {DOC}.",
    ], stream)


def _wrap(text, width):
    out, line = [], ""
    for word in text.split():
        if line and len(line) + 1 + len(word) > width:
            out.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        out.append(line)
    return out
