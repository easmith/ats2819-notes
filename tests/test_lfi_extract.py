"""Pulling the firmware's own files out of a logical image, which identification depends on."""
from conftest import ROOT

from lfi_extract import members


def test_the_stock_image_yields_its_47_files_with_valid_checksums():
    image = (ROOT / "firmware/stock/nor_logical_1MB.bin").read_bytes()
    found = members(image)
    assert len(found) == 47
    names = {name for name, _ in found}
    assert b"TTS_RC  LIB" in names and b"CONFIG  BIN" in names


def test_the_extracted_prompt_library_matches_the_published_stock_copy():
    image = (ROOT / "firmware/stock/nor_logical_1MB.bin").read_bytes()
    blob = dict(members(image))[b"TTS_RC  LIB"]
    stock = (ROOT / "firmware/stock/tts_rc_device.lib").read_bytes()
    assert blob[:len(stock)] == stock
