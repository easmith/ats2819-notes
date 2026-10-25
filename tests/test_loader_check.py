"""An unexpected loader revision must warn loudly and still let the operator decide."""
import hashlib
import io
import struct

from loader_check import (BREC_OFF_SITES, KNOWN_NORFWSC_SIZE, LOADER_ADDR, SEED_SEL_SITE,
                          check_norfwsc, warn, warn_sw_seed)


def make_loader() -> bytes:
    """A blob shaped like the known NorFwsc: right size, right bytes at every patch site."""
    b = bytearray(KNOWN_NORFWSC_SIZE)
    struct.pack_into("<I", b, SEED_SEL_SITE - LOADER_ADDR, 0x34794000)
    for va in BREC_OFF_SITES:
        o = va - LOADER_ADDR
        b[o:o + 4] = b"\x00\xf4\x12\x48"
    return bytes(b)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_known_loader_is_clean():
    data = make_loader()
    v = check_norfwsc(data, known_sha=sha(data))
    assert v.known and v.seed_site_ok and all(v.brec_sites_ok) and v.problems == []


def test_unknown_sha_is_a_problem_not_an_exception():
    v = check_norfwsc(make_loader())
    assert not v.known and v.problems


def test_wrong_seed_site_is_reported():
    data = bytearray(make_loader())
    o = SEED_SEL_SITE - LOADER_ADDR
    data[o:o + 4] = b"\x00\x00\x00\x00"
    v = check_norfwsc(bytes(data), known_sha=sha(bytes(data)))
    assert not v.seed_site_ok and v.problems


def test_one_wrong_brec_site_is_pinpointed():
    data = bytearray(make_loader())
    o = BREC_OFF_SITES[1] - LOADER_ADDR
    data[o:o + 4] = b"\x11\x22\x33\x44"
    v = check_norfwsc(bytes(data), known_sha=sha(bytes(data)))
    assert v.brec_sites_ok == (True, False, True)


def test_short_blob_is_reported_not_crashed():
    v = check_norfwsc(b"\x00" * 16)
    assert not v.known and v.problems


def test_warn_prints_a_box_and_returns_none():
    buf = io.StringIO()
    assert warn(check_norfwsc(b"\x00" * 16), buf) is None
    out = buf.getvalue()
    assert "WARNING" in out and "brick" in out.lower() and "07-recovery" in out


def test_warn_is_silent_for_a_clean_verdict():
    data = make_loader()
    buf = io.StringIO()
    warn(check_norfwsc(data, known_sha=sha(data)), buf)
    assert buf.getvalue() == ""


def test_sw_seed_mode_warns_about_bricking():
    buf = io.StringIO()
    assert warn_sw_seed(buf) is None
    out = buf.getvalue()
    assert "brick" in out.lower() and "--sw-seed" in out


def test_patching_an_unexpected_loader_skips_the_site_instead_of_crashing():
    """A loader that does not match must come back unpatched, not raise AssertionError."""
    import adfu_flash
    blob = bytes(KNOWN_NORFWSC_SIZE)          # all zeros: no patch site matches
    assert adfu_flash.fix_brec_offset(blob) == blob
    assert adfu_flash.use_hw_seed(blob) == blob


def test_patching_the_known_loader_still_applies_both_edits():
    import adfu_flash
    data = make_loader()
    brec_patched = adfu_flash.fix_brec_offset(data)
    assert brec_patched != data
    for va in BREC_OFF_SITES:
        o = va - LOADER_ADDR
        assert brec_patched[o:o + 2] == (0xF7C1).to_bytes(2, "little")
    seed_patched = adfu_flash.use_hw_seed(data)
    o = SEED_SEL_SITE - LOADER_ADDR
    assert struct.unpack_from("<I", seed_patched, o)[0] == 0x34790000


def test_an_unidentified_loader_is_checked_for_patch_sites_but_not_identity():
    """The `ours` path uploads the FWSC from its package, whose hash is not the vendor file's."""
    data = make_loader()
    v = check_norfwsc(data, known_sha=None)
    assert v.seed_site_ok and all(v.brec_sites_ok) and v.problems == []
