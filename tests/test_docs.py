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


def test_every_relative_link_resolves():
    broken = []
    for path in markdown_files():
        for target in LINK.findall(path.read_text()):
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            resolved = (path.parent / target.split("#")[0]).resolve()
            if not resolved.exists():
                broken.append(f"{path.relative_to(ROOT)} -> {target}")
    assert broken == []


def test_readme_states_scope_and_license():
    t = (ROOT / "README.md").read_text()
    assert "ATS2819" in t and "AGPTEK S22" in t and "CC BY 4.0" in t and "macOS" in t


def test_each_readme_links_its_own_safety_page():
    assert "SAFETY.md" in (ROOT / "README.md").read_text()
    assert "SAFETY.ru.md" in (ROOT / "README.ru.md").read_text()
