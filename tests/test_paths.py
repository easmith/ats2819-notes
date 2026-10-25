"""Paths must tell a reader what to run, not raise a bare FileNotFoundError."""
import pytest
from conftest import ROOT

import paths
from paths import VENDOR_ROOT, BootstrapMissing, asset, image_or_shipped, vendor


def test_vendor_missing_names_bootstrap():
    with pytest.raises(BootstrapMissing) as e:
        vendor("2819p", "psp_rel", "bin", "upgrade.bin")
    assert "scripts/bootstrap.sh" in str(e.value)


def test_asset_returns_existing_path():
    assert asset("firmware", "stock", "nor_logical_1MB.bin").exists()


def test_asset_missing_file_reports_its_path():
    with pytest.raises(FileNotFoundError) as e:
        asset("firmware", "stock", "no_such_file.bin")
    assert "firmware/stock/no_such_file.bin" in str(e.value)


def test_vendor_root_is_inside_the_repo():
    assert VENDOR_ROOT == ROOT / "vendor"


def test_image_or_shipped_falls_back_to_the_published_image(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "BUILD_ROOT", tmp_path)
    assert image_or_shipped("afi_plain.bin") == ROOT / "firmware/prompts-ru-en/afi_plain.bin"


def test_image_or_shipped_prefers_a_freshly_built_one(monkeypatch, tmp_path):
    (tmp_path / "fw").mkdir()
    built = tmp_path / "fw" / "afi_plain.bin"
    built.write_bytes(b"x")
    monkeypatch.setattr(paths, "BUILD_ROOT", tmp_path)
    assert image_or_shipped("afi_plain.bin") == built
