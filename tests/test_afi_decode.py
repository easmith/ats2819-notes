"""Decoding the SDK package must reproduce the exact loaders these tools were built against."""
import hashlib

import pytest
from conftest import ROOT

from afi_decode import decode, extract
from loader_check import KNOWN_NORFWSC_SHA256

SDK_AFI = ROOT / "vendor" / "2819p" / "psp_rel" / "bin" / "AFI.bin"


def test_decode_is_xor_38_and_involutive():
    assert decode(b"\x00\x38\xff") == b"\x38\x00\xc7"
    assert decode(decode(b"abc")) == b"abc"


@pytest.mark.skipif(not SDK_AFI.is_file(), reason="run scripts/bootstrap.sh first")
def test_decoding_the_sdk_package_yields_the_known_loader(tmp_path):
    written = extract(SDK_AFI, tmp_path)
    assert (tmp_path / "NorFwsc.bin") in written
    digest = hashlib.sha256((tmp_path / "NorFwsc.bin").read_bytes()).hexdigest()
    assert digest == KNOWN_NORFWSC_SHA256


def test_the_packages_own_revision_suffix_is_stripped():
    from afi_decode import plain_name
    assert plain_name("NorFwsc.bin~0") == "NorFwsc.bin"
    assert plain_name("Production.py~12") == "Production.py"
    assert plain_name("nor_id.bin") == "nor_id.bin"
