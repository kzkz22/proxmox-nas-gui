"""The translation dictionaries.

They are split one file per package and merged at load time, so nothing at
runtime notices a key that only exists in one language or that was lost in the
split - t() silently falls back to printing the key itself.
"""

import json
import re
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
I18N = FRONTEND / "i18n"
BUNDLES = ("core", "samba", "storage")
LANGS = ("en", "hu")

# The full key set before the per-package split. The union of the split files
# must still equal this, so the split provably lost nothing.
EXPECTED_KEY_COUNT = 206


def load(bundle: str, lang: str) -> dict:
    return json.loads((I18N / bundle / f"{lang}.json").read_text())


def merged(lang: str) -> dict:
    out = {}
    for bundle in BUNDLES:
        out.update(load(bundle, lang))
    return out


@pytest.mark.parametrize("bundle", BUNDLES)
def test_languages_have_the_same_keys(bundle):
    en, hu = load(bundle, "en"), load(bundle, "hu")
    assert set(en) == set(hu), (
        f"{bundle}: only in en {sorted(set(en) - set(hu))}, "
        f"only in hu {sorted(set(hu) - set(en))}"
    )


def test_no_key_appears_in_two_bundles():
    """Merging is a plain update, so a duplicated key would let one bundle
    silently override another."""
    seen = {}
    for bundle in BUNDLES:
        for key in load(bundle, "en"):
            assert key not in seen, f"{key} in both {seen[key]} and {bundle}"
            seen[key] = bundle


def test_the_split_did_not_lose_keys():
    assert len(merged("en")) == EXPECTED_KEY_COUNT


@pytest.mark.parametrize("lang", LANGS)
def test_no_translation_is_empty(lang):
    empty = [k for k, v in merged(lang).items() if not str(v).strip()]
    assert not empty, f"{lang}: empty translations {empty}"


@pytest.mark.parametrize("lang", LANGS)
def test_every_key_used_in_the_frontend_exists(lang):
    """Catches a nav label or page string left behind by a refactor. t() would
    otherwise just render the raw key."""
    dictionary = merged(lang)
    used = set()
    for js in FRONTEND.rglob("*.js"):
        # Only complete literals: t("share.new"), not the "access." half of
        # t("access." + level), which is covered by the prefix test below.
        used |= set(re.findall(r'\bt\(\s*"([\w.]+)"\s*[,)]', js.read_text()))
    used |= set(re.findall(r'data-i18n="([\w.]+)"', (FRONTEND / "index.html").read_text()))
    # Registry-declared nav labels are referenced as data, not as t("...").
    used |= set(re.findall(r'navKey:\s*"([\w.]+)"',
                           "".join(p.read_text() for p in FRONTEND.rglob("pages.js"))))

    missing = sorted(k for k in used if k not in dictionary)
    assert not missing, f"{lang}: keys used but not translated: {missing}"


@pytest.mark.parametrize("lang", LANGS)
def test_every_dynamic_key_prefix_has_translations(lang):
    """Keys built at runtime - t("policy." + name) - cannot be checked
    exactly, but an entirely untranslated prefix is still a real bug."""
    dictionary = merged(lang)
    prefixes = set()
    for js in FRONTEND.rglob("*.js"):
        prefixes |= set(re.findall(r'\bt\(\s*"([\w.]+\.)"\s*\+', js.read_text()))

    assert prefixes, "expected the concatenated t() calls to be found"
    empty = sorted(p for p in prefixes if not any(k.startswith(p) for k in dictionary))
    assert not empty, f"{lang}: no translations under prefixes {empty}"


def test_every_page_in_the_registry_has_a_nav_label():
    registry = "".join(p.read_text() for p in FRONTEND.rglob("pages.js"))
    ids = re.findall(r'id:\s*"(\w+)"', registry)
    nav_keys = re.findall(r'navKey:\s*"([\w.]+)"', registry)
    assert len(ids) == len(nav_keys) == 6
    assert set(nav_keys) <= set(merged("hu"))
