"""The free space before VRAM is the one number a reader must not be told wrongly."""
import build_fw
import paths
import pytest


def test_margin_is_measured_from_where_the_lfi_actually_starts():
    # the shipped image: lfi_cap 0xE9900 at LFI_BASE 0x4280 -> 0x2480 free, not 0x6700
    assert build_fw.vram_margin(0xE9900) == 0x2480


def test_an_image_whose_lfi_would_reach_vram_does_not_fit():
    assert build_fw.lfi_fits(0xE9900)
    assert not build_fw.lfi_fits(0xF0000 - build_fw.LFI_BASE + 0x40)


def test_the_fwu_reference_is_optional_and_says_so(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "VENDOR_ROOT", tmp_path)
    assert build_fw.fwu_reference() is None


def test_expected_image_leaves_mbrec_erased_when_it_was_not_written():
    """--no-mbrec must not make a correct write look like a failure."""
    import adfu_flash
    mbrec, brec, lfi = b"\x5a" * 0x400, b"\x11" * 0x3280, b"\x22" * 0x100
    without = adfu_flash.expected_logical(mbrec, brec, lfi, with_mbrec=False)
    assert without[:0x400] == b"\xff" * 0x400
    assert adfu_flash.expected_logical(mbrec, brec, lfi)[:0x400] == mbrec
