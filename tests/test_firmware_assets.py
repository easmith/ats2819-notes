"""The published images must be the ones that were verified on the device, and free of pairing data."""
import hashlib
import struct

from conftest import PROMPT_IDS, ROOT
from privacy_scan import scan


def test_stock_logical_image_size():
    assert (ROOT / "firmware/stock/nor_logical_1MB.bin").stat().st_size == 1048576


def test_raw_reference_is_truncated_to_first_megabyte():
    assert (ROOT / "firmware/raw/raw_first_1MB.bin").stat().st_size == 1048576


def test_tts_rc_lib_header_and_both_language_slots():
    d = (ROOT / "firmware/prompts-ru-en/tts_rc.lib").read_bytes()
    assert len(d) == 87280 and d[:4] == b"MP3 " and d[8:12] == b"v2  "
    for pid in PROMPT_IDS:
        for lang in (0, 1):
            off, length = struct.unpack_from("<IH", d, 0x10 + 8 * (2 * pid + lang))
            assert length > 0 and off >= 0x400 and off + length <= len(d)


def test_sha256sums_match():
    lines = [l for l in (ROOT / "firmware/SHA256SUMS").read_text().splitlines()
             if l.strip() and not l.startswith("#")]
    assert lines, "manifest is empty"
    for line in lines:
        digest, name = line.split(maxsplit=1)
        blob = (ROOT / name.strip()).read_bytes()
        assert hashlib.sha256(blob).hexdigest() == digest, name


def test_every_published_binary_is_in_the_manifest():
    listed = {l.split(maxsplit=1)[1].strip()
              for l in (ROOT / "firmware/SHA256SUMS").read_text().splitlines()
              if l.strip() and not l.startswith("#")}
    on_disk = {str(p.relative_to(ROOT)) for p in (ROOT / "firmware").rglob("*")
               if p.is_file() and p.suffix != ".md" and p.name != "SHA256SUMS"}
    assert on_disk == listed


def test_firmware_tree_has_no_personal_strings():
    assert scan([ROOT / "firmware"]) == []
