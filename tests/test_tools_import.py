"""Every tool must import on a bare Python, with no SDK and no pyusb present."""
import importlib

import pytest

ALL_TOOLS = ("adfu_core", "adfu_flash", "adfu_stub", "build_fw", "cfgdump", "extract_all",
             "fwfat", "fwu_pack", "loader_check", "make_tts", "norline", "verify_raw")


@pytest.mark.parametrize("mod", ALL_TOOLS)
def test_tool_imports_without_sdk_or_pyusb(mod):
    importlib.import_module(mod)


def test_missing_dependency_points_at_bootstrap():
    import adfu_core
    from paths import BootstrapMissing
    with pytest.raises(BootstrapMissing) as e:
        adfu_core._import_or_hint("no_such_module_xyz")
    assert "scripts/bootstrap.sh" in str(e.value)


def test_open_adfu_without_device_gives_actionable_message(monkeypatch):
    import adfu_core

    class FakeUsb:
        class core:
            @staticmethod
            def find(**kwargs):
                return None

    class FakeProtocol:
        VID, PID_ROM = 0x10D6, 0x10D6

    monkeypatch.setattr(adfu_core, "_usb", lambda: FakeUsb)
    monkeypatch.setattr(adfu_core, "P", FakeProtocol)
    monkeypatch.setattr(adfu_core, "switch_to_download", lambda: None)
    with pytest.raises(RuntimeError) as e:
        adfu_core.open_adfu()
    message = str(e.value)
    assert "ADFU" in message and "sudo" in message


def test_a_missing_usb_backend_names_libusb(monkeypatch):
    """Without libusb, pyusb raises NoBackendError; a reader must be told what to install."""
    import adfu_core

    class FakeUsb:
        class core:
            class NoBackendError(Exception):
                pass

            @staticmethod
            def find(**kwargs):
                raise FakeUsb.core.NoBackendError("no backend available")

    class FakeProtocol:
        VID, PID_ROM = 0x10D6, 0x10D6

    monkeypatch.setattr(adfu_core, "_usb", lambda: FakeUsb)
    monkeypatch.setattr(adfu_core, "P", FakeProtocol)
    with pytest.raises(RuntimeError) as e:
        adfu_core.open_adfu()
    assert "libusb" in str(e.value)


def test_no_usb_permission_explains_itself(monkeypatch):
    """Without sudo, pyusb raises USBError from set_configuration — not something a reader can read."""
    import adfu_core

    class FakeDevice:
        def set_configuration(self):
            raise FakeUsb.core.USBError("[Errno 13] Access denied (insufficient permissions)")

    class FakeUsb:
        class core:
            class NoBackendError(Exception):
                pass

            class USBError(Exception):
                pass

            @staticmethod
            def find(**kwargs):
                return FakeDevice()

    class FakeProtocol:
        VID, PID_ROM = 0x10D6, 0x10D6

    monkeypatch.setattr(adfu_core, "_usb", lambda: FakeUsb)
    monkeypatch.setattr(adfu_core, "P", FakeProtocol)
    with pytest.raises(RuntimeError) as e:
        adfu_core.open_adfu()
    message = str(e.value)
    assert "sudo" in message and "Access denied" in message
