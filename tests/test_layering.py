"""The package boundaries, enforced.

The point of splitting the code into core/samba/storage was that the two
feature halves stop depending on each other, so that either could be worked on
- or eventually extracted - without dragging the other along. Nothing else
notices when an import quietly crosses back.

Two rules:
  1. samba and storage never import each other.
  2. core never imports a feature package; features depend on core, and the
     composition roots (models.py, routes.py, state_view.py, main.js) are the
     only places allowed to see both.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend" / "app"
FRONTEND = ROOT / "frontend"

# Allowed to reach into both halves - that is their job.
COMPOSITION_ROOTS = {
    BACKEND / "models.py",
    BACKEND / "routes.py",
    BACKEND / "state_view.py",
    FRONTEND / "main.js",
    FRONTEND / "pages.js",
}


def python_files(package: str) -> list[Path]:
    return sorted(p for p in (BACKEND / package).rglob("*.py"))


def js_files(package: str) -> list[Path]:
    return sorted((FRONTEND / package).rglob("*.js"))


def code_without_comments(path: Path) -> str:
    """Strip comments and docstrings so prose about the other package does not
    read as a dependency on it."""
    text = path.read_text()
    if path.suffix == ".py":
        text = re.sub(r'""".*?"""', "", text, flags=re.DOTALL)
        text = re.sub(r"^\s*#.*$", "", text, flags=re.MULTILINE)
    else:
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
        text = re.sub(r"^\s*//.*$", "", text, flags=re.MULTILINE)
    return text


def imports_in(path: Path) -> list[str]:
    code = code_without_comments(path)
    if path.suffix == ".py":
        return re.findall(r"^\s*(?:from|import)\s+([\w.]+)", code, re.MULTILINE)
    return re.findall(r'from\s+["\']([^"\']+)["\']', code)


@pytest.mark.parametrize(
    "package,forbidden", [("samba", "storage"), ("storage", "samba")]
)
def test_backend_feature_packages_do_not_import_each_other(package, forbidden):
    offenders = [
        f"{p.relative_to(ROOT)}: {imp}"
        for p in python_files(package)
        for imp in imports_in(p)
        if re.search(rf"(^|\.){forbidden}(\.|$)", imp)
    ]
    assert not offenders, offenders


@pytest.mark.parametrize(
    "package,forbidden", [("samba", "storage"), ("storage", "samba")]
)
def test_frontend_feature_packages_do_not_import_each_other(package, forbidden):
    offenders = [
        f"{p.relative_to(ROOT)}: {spec}"
        for p in js_files(package)
        for spec in imports_in(p)
        if f"/{forbidden}/" in spec or spec.startswith(f"./{forbidden}/")
    ]
    assert not offenders, offenders


def test_backend_core_does_not_import_the_feature_packages():
    offenders = [
        f"{p.relative_to(ROOT)}: {imp}"
        for p in python_files("core")
        for imp in imports_in(p)
        if re.search(r"(^|\.)(samba|storage)(\.|$)", imp)
    ]
    assert not offenders, offenders


def test_frontend_core_does_not_import_the_feature_packages():
    offenders = [
        f"{p.relative_to(ROOT)}: {spec}"
        for p in js_files("core")
        for spec in imports_in(p)
        if "/samba/" in spec or "/storage/" in spec
    ]
    assert not offenders, offenders


def test_only_composition_roots_see_both_halves():
    both = []
    for path in list(BACKEND.rglob("*.py")) + list(FRONTEND.rglob("*.js")):
        if path in COMPOSITION_ROOTS:
            continue
        text = " ".join(imports_in(path))
        if re.search(r"(^|\W)\S*samba\S*", text) and re.search(r"(^|\W)\S*storage\S*", text):
            both.append(str(path.relative_to(ROOT)))
    assert not both, f"modules depending on both halves: {both}"


def test_the_composition_roots_exist():
    """Guards the guard: a renamed root would silently become exempt-by-
    absence rather than exempt-by-intent."""
    missing = [str(p.relative_to(ROOT)) for p in COMPOSITION_ROOTS if not p.exists()]
    assert not missing, missing
