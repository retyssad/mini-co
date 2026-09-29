"""Session-scoped undo for file mutations.

edit_file and write_file record a checkpoint before touching a file; /undo
pops the latest one and restores the previous bytes (or removes the file if
it did not exist). In-memory only: undo history dies with the process, and
bash side effects are not tracked, only the two file-writing tools.
"""

import difflib
from pathlib import Path

# (path, prior bytes or None if the file did not exist)
_stack: list[tuple[str, bytes | None]] = []


def record(path: Path) -> None:
    """Capture the pre-mutation state of path. Call right before writing."""
    _stack.append((str(path), path.read_bytes() if path.exists() else None))


def undo() -> str:
    """Restore the most recent checkpoint."""
    if not _stack:
        return "Nothing to undo."
    path_str, prior = _stack.pop()
    p = Path(path_str)
    if prior is None:
        p.unlink(missing_ok=True)
        return f"Removed {path_str} (created this session)."
    # the parent tree may be gone by now: bash side effects are untracked, so
    # recreate it instead of dying on FileNotFoundError
    recreated = not p.parent.exists()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(prior)
    if recreated:
        return f"Restored {path_str} (recreated missing parent directories)."
    return f"Restored {path_str}."


def pending() -> int:
    return len(_stack)


def changed_paths() -> list[str]:
    originals = {path: prior for path, prior in reversed(_stack)}
    return sorted(
        path for path, prior in originals.items()
        if (Path(path).read_bytes() if Path(path).exists() else None) != prior
    )


def diff_for(path: str) -> str:
    prior = next((data for name, data in _stack if name == path), None)
    if not any(name == path for name, _ in _stack):
        return "No checkpoint for this file."
    current = Path(path).read_bytes() if Path(path).exists() else b""
    try:
        before = (prior or b"").decode("utf-8").splitlines(keepends=True)
        after = current.decode("utf-8").splitlines(keepends=True)
    except UnicodeDecodeError:
        return "Binary file changed."
    return "".join(difflib.unified_diff(before, after, fromfile="a/" + path, tofile="b/" + path)) or "No changes."


def clear() -> None:
    _stack.clear()
