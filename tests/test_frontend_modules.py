"""Static checks over the frontend ES modules.

There is no JS toolchain and no bundler, so nothing else would notice a
mistyped import until the page fails to load in a browser. These checks are
plain text analysis and need no Node.
"""

import re
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
MODULES = sorted(FRONTEND.rglob("*.js"))

IMPORT_RE = re.compile(r'import\s+\{([^}]*)\}\s+from\s+["\']([^"\']+)["\']')
EXPORT_RE = re.compile(r"export\s+(?:async\s+)?(?:function|class|const|let|var)\s+([\w$]+)")


def exports_of(path: Path) -> set[str]:
    return set(EXPORT_RE.findall(path.read_text()))


def imports_of(path: Path) -> list[tuple[Path, list[str]]]:
    out = []
    for names, spec in IMPORT_RE.findall(path.read_text()):
        target = (path.parent / spec).resolve()
        out.append((target, [n.strip() for n in names.split(",") if n.strip()]))
    return out


def rel(path: Path) -> str:
    return str(path.relative_to(FRONTEND))


def test_modules_are_found():
    """Guards the guard: an empty module list would make everything below
    pass vacuously."""
    assert len(MODULES) > 10


@pytest.mark.parametrize("module", MODULES, ids=rel)
def test_every_import_resolves(module):
    for target, names in imports_of(module):
        assert target.exists(), f"{rel(module)} imports missing file {target}"
        available = exports_of(target)
        missing = [n for n in names if n not in available]
        assert not missing, f"{rel(module)} imports {missing} not exported by {rel(target)}"


def test_no_import_cycles():
    """ES module cycles resolve for hoisted function declarations but throw a
    TDZ error for const arrow functions, so a cycle is a latent crash that
    only fires on whichever path happens to run first."""
    graph = {m: [t for t, _ in imports_of(m)] for m in MODULES}
    cycles, state = [], {}

    def visit(node, stack):
        if state.get(node) == "open":
            path = stack[stack.index(node):] + [node]
            cycles.append(" -> ".join(rel(p) for p in path))
            return
        if state.get(node) == "done":
            return
        state[node] = "open"
        for target in graph.get(node, []):
            visit(target, stack + [node])
        state[node] = "done"

    for module in MODULES:
        visit(module, [])
    assert not cycles, "import cycles: " + "; ".join(sorted(set(cycles)))


def test_every_module_is_reachable_from_the_entry_point():
    """A module nobody imports is dead code that still looks maintained."""
    imported = {t for m in MODULES for t, _ in imports_of(m)}
    orphans = [rel(m) for m in MODULES if m not in imported and m.name != "main.js"]
    assert not orphans, f"unreachable modules: {orphans}"


def test_index_html_loads_the_entry_point_as_a_module():
    """Without type=module the imports are a syntax error, and .mjs would
    depend on the host's mimetypes table knowing that extension."""
    html = (FRONTEND / "index.html").read_text()
    assert '<script type="module" src="main.js"></script>' in html
    assert "app.js" not in html


def test_no_module_uses_the_mjs_extension():
    assert not list(FRONTEND.rglob("*.mjs"))


def test_state_is_only_written_by_its_owning_module():
    """S is exported as a live binding: importers can read it, but assigning
    to an imported binding is a runtime error. Both writes stay in api.js."""
    for module in MODULES:
        if module.name == "api.js":
            continue
        text = module.read_text()
        assert not re.search(r"^\s*S\s*=", text, re.MULTILINE), (
            f"{rel(module)} assigns to the imported S binding"
        )
