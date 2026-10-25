"""Both language branches must stay in step, and every link must lead somewhere."""
import re

from conftest import ROOT

LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


def markdown_files():
    return [p for p in ROOT.rglob("*.md")
            if not {".git", ".superpowers", "vendor", ".venv"}.intersection(p.parts)]


def test_en_and_ru_have_the_same_file_set():
    assert sorted(p.name for p in (ROOT / "docs/en").glob("*.md")) == \
           sorted(p.name for p in (ROOT / "docs/ru").glob("*.md"))


def looks_like_a_path(target):
    """Whether a markdown link target is meant to be a file in this repository.

    notes/ keeps raw disassembly, and a line like `jalr [*(0x9fc21100)+0x18](10)` parses as a
    markdown link with the target "10". A target with neither a slash nor a dot is not a path.
    """
    if target.startswith(("http://", "https://", "mailto:", "#")):
        return False
    return "/" in target or "." in target


def test_every_relative_link_resolves():
    broken = []
    for path in markdown_files():
        for target in LINK.findall(path.read_text()):
            if not looks_like_a_path(target):
                continue
            resolved = (path.parent / target.split("#")[0]).resolve()
            if not resolved.exists():
                broken.append(f"{path.relative_to(ROOT)} -> {target}")
    assert broken == []


def test_disassembly_in_notes_is_not_mistaken_for_a_link():
    assert not looks_like_a_path("10")
    assert looks_like_a_path("01-device.md")
    assert looks_like_a_path("../../notes")


def test_readme_states_scope_and_license():
    t = (ROOT / "README.md").read_text()
    assert "ATS2819" in t and "CC BY 4.0" in t and "macOS" in t


def test_no_document_claims_a_retail_name_for_the_device():
    """The product name is not known: `B18` and `AGP-S22` are what the device calls itself."""
    claimed = [str(p.relative_to(ROOT)) for p in markdown_files() if "AGPTEK" in p.read_text()]
    assert claimed == [], claimed


def test_readme_identifies_the_device_by_what_it_reports():
    t = (ROOT / "README.md").read_text()
    assert "B18" in t and "AGP-S22" in t and "libusb" in t


def test_each_readme_links_its_own_safety_page():
    assert "SAFETY.md" in (ROOT / "README.md").read_text()
    assert "SAFETY.ru.md" in (ROOT / "README.ru.md").read_text()
