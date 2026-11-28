"""
Deterministic test selection for the three test levels (FAST / RELATED / FULL). Read-only: it parses source files,
it never imports application code.

    python backend/scripts/select_tests.py                     # changed files vs main + working tree; prints the plan
    python backend/scripts/select_tests.py --level fast --run  # run the FAST selection
    python backend/scripts/select_tests.py --level related --run
    python backend/scripts/select_tests.py --files backend/app/content/topic_coverage.py --level related

How a test is linked to a changed module (no filename guessing):
  * every ``import`` / ``from ... import`` anywhere in the file (module level and inside functions), relative imports
    resolved, ``from pkg import submodule`` counted as the submodule, ``app.x`` and ``backend.app.x`` treated as one;
  * dotted strings naming an app module (``monkeypatch.setattr("backend.app.x.y", ...)``, ``importlib`` targets);
  * test helper modules (``topic_fakes_331``, ``test_community_posts_332e`` imported by other test files) pass their
    imports on to every test file that imports them, transitively;
  * a changed non-Python file (template, JSON data) selects tests and modules that mention its file name.

FAST    = tests that import a changed module or a module importing it directly (through test helpers too) + changed
          test files + the essential safety set; slow-marked tests excluded.
RELATED = tests that import a changed module or its importers up to --depth levels (default 2) + changed test files +
          the essential set; slow tests included. Hubs (modules importing >= HUB_FAN_OUT app modules: app.main, the
          orchestrator, ...) are never expanded; a hub's tests join only when it imports a changed module directly.
FULL    = the whole suite. Required (and reported) when a change touches shared architecture (FULL_TRIGGERS).
"""
from __future__ import annotations

import argparse
import ast
import os
import re
import subprocess
import sys
from collections import defaultdict, deque
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "backend" / "app"
TESTS = ROOT / "backend" / "tests"

# Shared architecture: a change here can break anything, so FULL is required (CLAUDE.md "Tests").
FULL_TRIGGERS = (
    "backend/app/scheduler/", "backend/app/orchestration/", "backend/app/database/", "backend/app/core/",
    "backend/app/templates/", "backend/app/main.py", "backend/app/autonomy/control_plane.py",
    "backend/tests/conftest.py", "pytest.ini", "requirements",
)
# Always part of FAST and RELATED: cheap guards for the rules that must never regress (test isolation, publishing
# and recovery safety, QC topic rule, retry policy). About 40 s together.
ESSENTIAL = (
    "test_final_safety_regressions.py", "test_publishing_safety.py", "test_recovery_safety_audit.py",
    "test_stage_3_21g_isolation_and_idempotency.py", "test_retry_policy_unlimited.py", "test_topic_coverage.py",
    "test_integrity_topic_keywords.py",
)
DOC_SUFFIXES = (".md", ".txt", ".rst")
HUB_FAN_OUT = 20             # a module importing this many app modules is a hub (see Graph.hubs)
# app.main imports nearly every module directly, so it is never pulled in as a dependant: its route tests that matter
# import the changed module themselves, and a change to main.py itself requires FULL.
NEVER_DEPENDANT = {"app.main"}
_DOTTED = re.compile(r"^(?:backend\.)?app(?:\.[A-Za-z_]\w*)+$")


# -- module graph --------------------------------------------------------------------------------------------------
def module_of(path: Path) -> Optional[str]:
    """backend/app/content/x.py -> 'app.content.x'; package __init__ -> the package."""
    try:
        rel = path.resolve().relative_to(APP.parent)
    except ValueError:
        return None
    parts = list(rel.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _app_modules() -> Dict[str, Path]:
    return {m: p for p in APP.rglob("*.py") if "__pycache__" not in p.parts and (m := module_of(p))}


def _canon(name: str, modules: Dict[str, Path]) -> Optional[str]:
    """'backend.app.x.y.Cls' -> the longest existing app module ('app.x.y')."""
    if name.startswith("backend.app"):
        name = name[len("backend."):]
    if not (name == "app" or name.startswith("app.")):
        return None
    parts = name.split(".")
    while parts:
        cand = ".".join(parts)
        if cand in modules:
            return cand
        parts.pop()
    return None


def imports_of(path: Path, modules: Dict[str, Path], test_helpers: Set[str]) -> Tuple[Set[str], Set[str]]:
    """(app modules, test helper modules) referenced anywhere in the file."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    except SyntaxError:
        return set(), set()
    own = module_of(path) or ""
    pkg = own.rsplit(".", 1)[0] if own and path.name != "__init__.py" else own
    found, helpers = set(), set()

    def add(name: str) -> None:
        if (m := _canon(name, modules)):
            found.add(m)
        top = name.split(".")[0]
        if top in test_helpers:
            helpers.add(top)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                add(a.name)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level and pkg:
                anchor = pkg.split(".")
                anchor = anchor[: len(anchor) - (node.level - 1)] if node.level > 1 else anchor
                base = ".".join(anchor + ([base] if base else []))
            add(base)
            for a in node.names:
                add(f"{base}.{a.name}")                       # `from pkg import submodule`
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and _DOTTED.match(node.value):
            add(node.value)
    return found, helpers


class Graph:
    def __init__(self) -> None:
        self.modules = _app_modules()
        self.test_files = sorted(p for p in TESTS.glob("test_*.py"))
        self.helpers = {p.stem: p for p in TESTS.glob("*.py") if p.name != "conftest.py"}
        self.deps: Dict[str, Set[str]] = {}
        self.rdeps: Dict[str, Set[str]] = defaultdict(set)
        for m, p in self.modules.items():
            d, _ = imports_of(p, self.modules, set())
            d.discard(m)
            self.deps[m] = d
            for x in d:
                self.rdeps[x].add(m)
        raw = {stem: imports_of(p, self.modules, set(self.helpers)) for stem, p in self.helpers.items()}
        self.test_imports: Dict[str, Set[str]] = {}
        self.test_helpers_used: Dict[str, Set[str]] = {}
        for stem in self.helpers:                                # close over helper -> helper imports
            mods, seen, queue = set(raw[stem][0]), {stem}, deque(raw[stem][1])
            while queue:
                h = queue.popleft()
                if h in seen or h not in raw:
                    continue
                seen.add(h)
                mods |= raw[h][0]
                queue.extend(raw[h][1])
            self.test_imports[stem] = mods
            self.test_helpers_used[stem] = seen - {stem}

        # Hubs import most of the application (app.main 68 modules, the orchestrator 41, ...). Expanding through them
        # would select nearly the whole suite, which is what FULL is for; they are never expanded further.
        self.hubs = {m for m, d in self.deps.items() if len(d) >= HUB_FAN_OUT}

    def dependants(self, mods: Iterable[str], depth: int, include_direct_hubs: bool) -> Set[str]:
        """The changed modules plus modules importing them, ``depth`` levels up; a hub is included only when it
        imports a changed module directly (and ``include_direct_hubs``) and is never expanded."""
        out, frontier = set(mods), set(mods)
        for level in range(1, depth + 1):
            nxt = {r for m in frontier for r in self.rdeps.get(m, ())} - out
            hubs = nxt & self.hubs
            if include_direct_hubs and level == 1:
                out |= hubs - NEVER_DEPENDANT
            nxt -= hubs
            out |= nxt
            frontier = nxt
        return out

    def tests_importing(self, mods: Set[str]) -> Set[str]:
        return {p.name for p in self.test_files if self.test_imports.get(p.stem, set()) & mods}


# -- changed files -------------------------------------------------------------------------------------------------
def changed_files(base: str) -> List[str]:
    def git(*args: str) -> List[str]:
        r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=False)
        return [l.strip().replace("\\", "/") for l in r.stdout.splitlines() if l.strip()]
    files = set(git("diff", "--name-only", f"{base}...HEAD")) | set(git("diff", "--name-only", "HEAD"))
    files |= set(git("ls-files", "--others", "--exclude-standard"))
    return sorted(files)


def required_level(files: List[str]) -> Tuple[str, List[str]]:
    triggers = [f for f in files if f.startswith(FULL_TRIGGERS)]
    if triggers:
        return "full", triggers
    if not files or all(f.endswith(DOC_SUFFIXES) or f.startswith("docs/") for f in files):
        return "none", []
    if any(f.startswith("backend/app/") for f in files):
        return "related", []
    return "fast", []


def select(graph: Graph, files: List[str], level: str, depth: int) -> Tuple[List[str], Dict[str, str]]:
    why: Dict[str, str] = {}
    changed_mods: Set[str] = set()
    text_refs: List[str] = []
    for f in files:
        p = ROOT / f
        if f.startswith("backend/tests/") and Path(f).name.startswith("test_") and f.endswith(".py"):
            why.setdefault(Path(f).name, "changed test file")
        elif f.startswith("backend/tests/") and f.endswith(".py") and Path(f).stem in graph.helpers:
            for t in graph.test_files:                             # a changed test helper
                if Path(f).stem in graph.test_helpers_used.get(t.stem, set()):
                    why.setdefault(t.name, f"uses changed test helper {Path(f).stem}")
        elif f.startswith("backend/app/") and f.endswith(".py"):
            m = module_of(p)
            if m:
                changed_mods.add(m)
        elif not f.endswith(DOC_SUFFIXES) and not f.startswith("docs/"):
            text_refs.append(Path(f).name)
    for name in text_refs:                                         # templates, JSON data, ...
        for t in graph.test_files:
            if name in t.read_text(encoding="utf-8", errors="ignore"):
                why.setdefault(t.name, f"mentions {name}")
        for m, p in graph.modules.items():
            if name in p.read_text(encoding="utf-8", errors="ignore"):
                changed_mods.add(m)
    for t in sorted(graph.tests_importing(changed_mods)):
        hit = sorted(graph.test_imports[Path(t).stem] & changed_mods)
        why.setdefault(t, f"imports {', '.join(hit[:3])}")
    # FAST: + tests of the modules that import a changed module directly (the behaviour reaches tests through them,
    # e.g. topic_coverage -> content_integrity_gate -> the CHECK_A tests), hubs excluded.
    # RELATED: two levels of importers, plus the tests of a hub that imports a changed module directly.
    affected = (graph.dependants(changed_mods, 1, include_direct_hubs=False) if level == "fast"
                else graph.dependants(changed_mods, depth, include_direct_hubs=True))
    for t in sorted(graph.tests_importing(affected) - set(why)):
        hit = sorted(graph.test_imports[Path(t).stem] & (affected - changed_mods))
        why.setdefault(t, f"imports dependant {', '.join(hit[:2])}")
    for t in ESSENTIAL:
        if (TESTS / t).is_file():
            why.setdefault(t, "essential safety set")
    return sorted(why), why


def pytest_command(tests: List[str], level: str) -> List[str]:
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-rfEs"]
    if level == "fast":
        cmd += ["-m", "not slow"]
    return cmd + ([str(TESTS / t) for t in tests] if level != "full" else [str(TESTS)])


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--level", choices=("auto", "fast", "related", "full"), default="auto",
                    help="auto = the level the rules require for these changes")
    ap.add_argument("--files", nargs="*", help="changed files (repo-relative); default: git diff vs --base")
    ap.add_argument("--base", default="main", help="git base for the diff (default: main)")
    ap.add_argument("--depth", type=int, default=2, help="RELATED: levels of reverse imports (default 2)")
    ap.add_argument("--run", action="store_true", help="run pytest on the selection")
    a = ap.parse_args(argv)
    files = [f.replace("\\", "/") for f in (a.files if a.files is not None else changed_files(a.base))]
    need, triggers = required_level(files)
    level = need if a.level == "auto" else a.level
    print(f"changed files: {len(files)}" + "".join(f"\n  {f}" for f in files))
    print(f"required level before commit: {need.upper()}"
          + (f" (shared architecture: {', '.join(triggers)})" if triggers else ""))
    if level == "none":
        print("no tests needed (documentation only)")
        return 0
    if a.level not in ("auto", "full") and need == "full":
        print(f"WARNING: running {level.upper()} for iteration only; FULL is required before commit/deploy.")
    if level == "full":
        tests, why = [], {}
        print("level FULL: the whole suite (serial)")
    else:
        graph = Graph()
        tests, why = select(graph, files, level, a.depth)
        print(f"level {level.upper()}: {len(tests)} test files" + (" (slow tests excluded)" if level == "fast" else ""))
        for t in tests:
            print(f"  {t:60} {why[t]}")
    cmd = pytest_command(tests, level)
    print("command: " + " ".join(f'"{c}"' if " " in c else c for c in cmd))
    if not a.run:
        return 0
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(ROOT), str(ROOT / "backend"), str(TESTS)])
    return subprocess.run(cmd, cwd=TESTS, env=env, check=False).returncode


if __name__ == "__main__":
    sys.exit(main())
