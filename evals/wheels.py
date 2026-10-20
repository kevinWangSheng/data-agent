"""The agent's locked dependencies as wheels for a task container's platform, so the container
installs them offline (ticket 20: fetching them from PyPI inside concurrent trials ran past
Harbor's 360-second agent setup).

`wheelhouse()` reads the container's platform from `PROBE`'s output (CPython version, glibc,
architecture and PEP 508 markers), takes each requirement of `uv export` whose marker holds there,
and picks the lock's wheel that best fits the container's tags among those whose hash the
requirement lists. Each wheel is fetched once into `CACHE_DIR`, outside the repo, and checked
against its sha256; a later run finds it there and goes to no network. A cold cache would cost
the first trials of a run the download, which at the bandwidth seen here can exceed the setup limit
by itself, so `ablate` fills the cache with `fill()` before it starts Harbor (ticket 22), for each
task's image (`task_image()`) as `docker run` probes it. By hand, for one image:

    uv run python -m evals.wheels ghcr.io/snowflake-labs/data-eng-bench-base:1.0.0
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from packaging.markers import Marker
from packaging.requirements import Requirement
from packaging.tags import Tag, compatible_tags, cpython_tags
from packaging.utils import canonicalize_name, parse_wheel_filename

CACHE_DIR = Path.home() / ".cache" / "data-agent" / "wheels"

# Run with the container's python3: PEP 508's marker environment (packaging.markers
# default_environment) and the C library, from the standard library alone.
PROBE = """\
import json, os, platform, sys
v = sys.implementation.version
level = "" if v.releaselevel == "final" else v.releaselevel[0] + str(v.serial)
print(json.dumps({
    "markers": {
        "implementation_name": sys.implementation.name,
        "implementation_version": f"{v.major}.{v.minor}.{v.micro}{level}",
        "os_name": os.name,
        "platform_machine": platform.machine(),
        "platform_python_implementation": platform.python_implementation(),
        "platform_release": platform.release(),
        "platform_system": platform.system(),
        "platform_version": platform.version(),
        "python_full_version": platform.python_version(),
        "python_version": ".".join(platform.python_version_tuple()[:2]),
        "sys_platform": sys.platform,
    },
    "libc": platform.libc_ver(),
}))
"""

# The glibc of the oldest manylinux tag (PEP 600), and the legacy names of manylinux tags.
OLDEST_GLIBC_MINOR = 5
LEGACY_MANYLINUX = {17: "manylinux2014", 12: "manylinux2010", 5: "manylinux1"}

_lock = threading.Lock()
_wheelhouses: dict["Platform", Path] = {}


@dataclass(frozen=True)
class Platform:
    machine: str
    python: tuple[int, int]
    glibc: tuple[int, int]
    markers: tuple[tuple[str, str], ...]

    @classmethod
    def from_probe(cls, stdout: str) -> "Platform":
        probe = json.loads(stdout)
        markers: dict[str, str] = probe["markers"]
        libc, libc_version = probe["libc"]
        if markers["implementation_name"] != "cpython":
            raise RuntimeError(f"the container's Python is not CPython: {markers}")
        if libc != "glibc":
            raise RuntimeError(f"the container's C library is not glibc: {libc!r}")
        major, minor = markers["python_version"].split(".")
        glibc_major, glibc_minor = libc_version.split(".")[:2]
        return cls(
            machine=markers["platform_machine"],
            python=(int(major), int(minor)),
            glibc=(int(glibc_major), int(glibc_minor)),
            markers=tuple(sorted(markers.items())),
        )

    def __str__(self) -> str:
        python = ".".join(map(str, self.python))
        glibc = ".".join(map(str, self.glibc))
        return f"CPython {python}, glibc {glibc}, {self.machine}"

    def tags(self) -> list[Tag]:
        """The wheel tags the container installs, most preferred first (packaging.tags)."""
        platforms: list[str] = []
        for minor in range(self.glibc[1], OLDEST_GLIBC_MINOR - 1, -1):
            platforms.append(f"manylinux_2_{minor}_{self.machine}")
            if minor in LEGACY_MANYLINUX:
                platforms.append(f"{LEGACY_MANYLINUX[minor]}_{self.machine}")
        platforms.append(f"linux_{self.machine}")
        abi = "cp{}{}".format(*self.python)
        return [
            *cpython_tags(self.python, abis=[abi], platforms=platforms),
            *compatible_tags(self.python, abi, platforms),
        ]


@dataclass(frozen=True)
class Wheel:
    name: str
    url: str
    sha256: str


def download(url: str, dest: Path) -> None:
    # Retries every error: TLS handshakes to GitHub and PyPI failed intermittently here.
    subprocess.run(
        ["curl", "-sSfL", "--retry", "5", "--retry-all-errors", "-o", str(dest), url], check=True
    )


def sha256(path: Path) -> str:
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def requirements_of(path: Path) -> list[tuple[Requirement, set[str]]]:
    """`uv export`'s requirements with the sha256 hashes each lists."""
    found: list[tuple[Requirement, set[str]]] = []
    logical = path.read_text().replace("\\\n", " ")
    for line in logical.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        spec, *options = line.split(" --hash=sha256:")
        found.append((Requirement(spec), {option.split()[0] for option in options}))
    return found


def lock_wheels(lock: Path) -> dict[tuple[str, str], list[Wheel]]:
    """uv.lock's wheels by package name and version."""
    wheels: dict[tuple[str, str], list[Wheel]] = {}
    for package in tomllib.loads(lock.read_text())["package"]:
        key = (canonicalize_name(package["name"]), package["version"])
        wheels[key] = [
            Wheel(
                name=wheel["url"].rsplit("/", 1)[-1],
                url=wheel["url"],
                sha256=wheel["hash"].removeprefix("sha256:"),
            )
            for wheel in package.get("wheels", [])
        ]
    return wheels


def select(requirements: Path, lock: Path, platform: Platform) -> list[Wheel]:
    """The wheel each requirement installs on the platform; one with no wheel there is an error."""
    environment = dict(platform.markers)
    rank = {tag: index for index, tag in enumerate(platform.tags())}
    locked = lock_wheels(lock)
    selected: list[Wheel] = []
    for requirement, hashes in requirements_of(requirements):
        if requirement.marker and not Marker.evaluate(requirement.marker, environment):
            continue
        (pin,) = requirement.specifier
        candidates: list[tuple[int, Wheel]] = []
        for wheel in locked.get((canonicalize_name(requirement.name), pin.version), []):
            ranks = [rank[tag] for tag in parse_wheel_filename(wheel.name)[3] if tag in rank]
            if ranks and wheel.sha256 in hashes:
                candidates.append((min(ranks), wheel))
        if not candidates:
            raise RuntimeError(
                f"no locked wheel of {requirement.name}=={pin.version} with a hash in the "
                f"requirements installs on {platform}"
            )
        selected.append(min(candidates, key=lambda candidate: candidate[0])[1])
    return selected


def fetch(url: str, expected: str) -> Path:
    """The file at url in CACHE_DIR, downloaded when the cache has no file with its sha256."""
    dest = CACHE_DIR / url.rsplit("/", 1)[-1]
    if dest.exists():
        if sha256(dest) == expected:
            return dest
        dest.unlink()  # cut short or replaced: fetched again
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(f"{dest.name}.{os.getpid()}-{threading.get_ident()}.part")
    try:
        download(url, part)
        if (digest := sha256(part)) != expected:
            raise RuntimeError(f"{dest.name}: sha256 {digest}, expected {expected}")
        part.replace(dest)
    finally:
        part.unlink(missing_ok=True)
    return dest


def wheelhouse(requirements: Path, lock: Path, platform: Platform) -> Path:
    """A directory holding the platform's wheels for the requirements, made once per process."""
    with _lock:
        if platform not in _wheelhouses:
            house = Path(tempfile.mkdtemp(prefix="data-agent-wheels-"))
            for wheel in select(requirements, lock, platform):
                cached = fetch(wheel.url, wheel.sha256)
                try:
                    (house / wheel.name).hardlink_to(cached)
                except OSError:
                    shutil.copyfile(cached, house / wheel.name)
            _wheelhouses[platform] = house
        return _wheelhouses[platform]


def task_image(task: Path) -> str:
    """The image of a Harbor task's container: task.toml's prebuilt `docker_image`, else the image
    environment/Dockerfile's last stage starts from, taken to have the built image's Python and C
    library (data-eng-bench's Dockerfiles only add files)."""
    environment = tomllib.loads((task / "task.toml").read_text()).get("environment", {})
    if image := environment.get("docker_image"):
        return str(image)
    dockerfile = task / "environment" / "Dockerfile"
    bases = [
        next((word for word in words[1:] if not word.startswith("--")), "")
        for words in map(str.split, dockerfile.read_text().splitlines())
        if words and words[0].upper() == "FROM"
    ]
    if not bases or not bases[-1] or "$" in bases[-1]:
        raise RuntimeError(f"{dockerfile}: no FROM image to probe the platform of")
    return bases[-1]


def probe(image: str) -> Platform:
    """The platform of an image's container, read with `docker run`."""
    proc = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "python3", image, "-c", PROBE],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"cannot probe {image}: {proc.stderr.strip()}")
    return Platform.from_probe(proc.stdout)


def fill(images: Iterable[str], requirements: Path, lock: Path) -> list[str]:
    """Fill the cache for the images' platforms, each platform once; returns one summary line per
    platform (its wheel count and the time taken)."""
    platforms = dict.fromkeys(probe(image) for image in sorted(set(images)))
    lines: list[str] = []
    for platform in platforms:
        started = time.monotonic()
        house = wheelhouse(requirements, lock, platform)
        count = len(list(house.glob("*.whl")))
        lines.append(
            f"{platform}: {count} wheels in {CACHE_DIR}, {time.monotonic() - started:.1f} s"
        )
    return lines


def main(argv: list[str]) -> None:
    """Fill the cache for the platform of an image (argv[0])."""
    from data_agent.run_config import REPO_ROOT
    from evals.harbor_agent import build_artifacts

    (image,) = argv
    _, requirements = build_artifacts()
    print("\n".join(fill([image], requirements, REPO_ROOT / "uv.lock")))


if __name__ == "__main__":
    main(sys.argv[1:])
