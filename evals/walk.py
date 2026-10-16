"""Walk a directory and report everything that could not be looked at, instead of skipping it."""

from pathlib import Path


def walk_files(root: Path) -> tuple[list[Path], list[str]]:
    """Regular files under root, plus a description of each directory or symlink not covered.

    `Path.rglob` silently drops unreadable directories; a guard that must see every file
    (scrub before upload, spend before the next batch) needs to know about them.
    """
    files: list[Path] = []
    missed: list[str] = []

    def unreadable(error: OSError) -> None:
        missed.append(f"{error.filename}: {error.strerror}")

    for directory, dirnames, filenames in root.walk(on_error=unreadable):
        for name in sorted(dirnames + filenames):
            path = directory / name
            if path.is_symlink():
                missed.append(f"{path}: symlink, not followed")
            elif name in filenames:
                files.append(path)
    return sorted(files), missed
