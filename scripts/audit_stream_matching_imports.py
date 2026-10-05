"""Audit static relative imports. This source audit does not establish HMR behavior."""
from pathlib import Path
import re

root = Path(__file__).resolve().parents[1] / "frontend" / "src"
files = set(root.rglob("*.js")) | set(root.rglob("*.vue"))
pattern = re.compile(r"(?:import|export)\s+(?:[^;]*?\s+from\s+)?['\"](\.[^'\"]+)['\"]")
graph = {}
for path in files:
    edges = set()
    for relative in pattern.findall(path.read_text()):
        target = (path.parent / relative).resolve()
        candidates = [target, target.with_suffix(".js"), target.with_suffix(".vue"), target / "index.js"]
        edges.update(candidate for candidate in candidates if candidate in files)
    graph[path] = edges


def reachable(start, target):
    pending = list(graph[start])
    seen = set()
    while pending:
        path = pending.pop()
        if path == target:
            return True
        if path not in seen:
            seen.add(path)
            pending.extend(graph[path])
    return False


for provider in ["codex", "claude_code"]:
    matching = root / "providers" / provider / "streamMatching.js"
    helper = matching.with_name("helpers.js")
    returns = reachable(matching, helper)
    print(f"{provider} matching-to-helper: {returns}")
    assert not returns
canonical = root / "providers" / "codex" / "canonical.js"
print("Codex canonical imports:", sorted(str(path.relative_to(root)) for path in graph[canonical]))
assert not graph[canonical]
