"""`scrub DIR`: remove API keys, the model endpoint, IPv4 addresses, hostnames, and the home
directories and user names of this machine and of the machine that ran the job before upload.

Rewrites every file under DIR in place (plan §7: published trajectories leak nothing). In `.json`
and `.jsonl` files it scrubs the decoded strings and re-serializes, so JSON escapes neither hide a
secret nor get broken by a redaction; other files, and lines that do not parse, are scrubbed as
text. JSON nested in a string (a tool that printed JSON) is decoded and scrubbed the same way.
Anything it cannot check (non-UTF-8 or unreadable files, unreadable directories, symlinks, a
`\\uXXXX` escape outside parseable JSON, a repeated JSON key) is listed and the command fails, and
so is any path left under a user's home directory (`/home/<x>`, `/Users/<x>`) that no rule
removed. Container paths such as `/app`, `/logs` and `/root` (the agent runs as root in the task
container, and the SDK saves long tool output under /root/.claude) are not host paths.
"""

import argparse
import getpass
import json
import os
import re
import sys
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from evals.walk import walk_files

# Not preceded by a name character, except as the last character of a JSON escape: tool output is
# often JSON serialized into a string, and in '{"output": "ok\nbuild-box-7"}' the host follows "n".
NAME_START = r"(?:(?<![A-Za-z0-9_-])|(?<=\\[bfnrt])|(?<=\\u[0-9A-Fa-f]{4}))"
KEY_SHAPE = re.compile(
    "|".join(
        [
            # DeepSeek, OpenAI and Anthropic keys; 20+ characters keeps "task-sk-x" prose out.
            rf"{NAME_START}sk-[A-Za-z0-9_-]{{20,}}",
            r"\b[Bb]earer\s+[A-Za-z0-9._~+/=-]{20,}",
            rf"{NAME_START}eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+",  # JWT
            rf"{NAME_START}gh[pousr]_[A-Za-z0-9]{{36,}}",  # GitHub token
            rf"{NAME_START}AKIA[0-9A-Z]{{16}}(?![0-9A-Z])",  # AWS access key id
        ]
    )
)
OCTET = r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
IPV4 = re.compile(rf"(?<![\d.]){OCTET}(?:\.{OCTET}){{3}}(?!\.?\d)")
# A user's home directory on a host; `[redacted-...]` is what a rule left in place of a name.
HOST_PATH = re.compile(
    r"(?:(?<![A-Za-z0-9_.-])|(?<=\\[bfnrt]))"
    r"(?:/(?:home|Users)/(?!\[redacted-)[^/\s\"'\\]+)"
)
# Can spell any character of a name, so no pattern sees "build\u002dbox-7" as "build-box-7".
UNICODE_ESCAPE = re.compile(r"\\u[0-9A-Fa-f]{4}")

# (marker kind, pattern): matches become "[redacted-<kind>]".
type Rules = list[tuple[str, re.Pattern[str]]]


class Unchecked(Exception):
    pass


def add_parser(commands: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    parser = commands.add_parser("scrub", help="remove keys, endpoints, IPs and hostnames in place")
    parser.add_argument("dir", type=Path)
    parser.add_argument(
        "--hostname", action="append", default=[], help="extra hostname to remove (repeatable)"
    )
    parser.add_argument(
        "--home",
        action="append",
        default=[],
        help="home directory of the machine that ran the job (repeatable; this one's is included)",
    )
    parser.add_argument(
        "--user",
        action="append",
        default=[],
        help="user name of the machine that ran the job (repeatable; this one's is included)",
    )
    parser.set_defaults(run=run)


def literal(value: str) -> re.Pattern[str]:
    return re.compile(re.escape(value), re.IGNORECASE)


def hostname(value: str) -> re.Pattern[str]:
    """Match a hostname as a whole label sequence, so "db" does not hit "db-1" or "duckdb"."""
    return re.compile(rf"{NAME_START}{re.escape(value)}(?![A-Za-z0-9_-])", re.IGNORECASE)


def build_rules(
    hostnames: list[str], homes: list[str] | None = None, users: list[str] | None = None
) -> Rules:
    """(marker kind, pattern) in apply order: keys first, a whole endpoint before its host."""
    rules: Rules = []
    if key := os.environ.get("DATA_AGENT_API_KEY", "").strip():
        rules.append(("key", literal(key)))
    rules.append(("key", KEY_SHAPE))
    hosts = [h for h in hostnames if h]
    if base_url := os.environ.get("DATA_AGENT_BASE_URL", "").strip():
        rules.append(("endpoint", literal(base_url.rstrip("/"))))
        netloc = base_url if "://" in base_url else f"//{base_url}"
        if host := urlsplit(netloc).hostname:
            hosts.append(host)
    rules.extend(("host", hostname(h)) for h in sorted(set(hosts), key=len, reverse=True))
    rules.append(("ip", IPV4))
    # Harbor records host paths (job, task, logs) under the home directory of the machine that
    # ran the job, which may not be this one; whole home paths go before the user names inside
    # them. A user name is removed as a path segment only, so a name that is also a word or a
    # Harbor field (`agent`) leaves prose and structure alone; root names no person.
    all_homes = {str(Path.home()), *(h.rstrip("/") for h in homes or [])} - {"", "/"}
    for home in sorted(all_homes, key=len, reverse=True):
        rules.append(("home", re.compile(rf"{re.escape(home)}(?![A-Za-z0-9_.-])")))
    all_users = {getpass.getuser(), *(users or [])} - {"", "root"}
    for user in sorted(all_users, key=len, reverse=True):
        rules.append(("user", re.compile(rf"(?<=/){re.escape(user)}(?![A-Za-z0-9_.-])")))
    return rules


def json_strings(text: str) -> Iterator[str]:
    """The decoded strings of text read as JSON or JSONL, and of JSON nested in them."""
    for document in {text, *text.split("\n")}:
        try:
            value = json.loads(document)
        except json.JSONDecodeError:
            continue
        yield from value_strings(value)


def value_strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
        yield from json_strings(value)
    elif isinstance(value, list):
        for v in value:
            yield from value_strings(v)
    elif isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from value_strings(v)


def host_paths(path: Path, text: str) -> list[str]:
    """`file:line: path` for each host home path in scrubbed text, then `file: path` for those
    only a JSON escape (`\\/`, `\\u002f`) kept out of sight."""
    found = [
        (f"{path}:{number}", match.group())
        for number, line in enumerate(text.splitlines(), start=1)
        for match in HOST_PATH.finditer(line)
    ]
    seen = {host_path for _, host_path in found}
    escaped = {m.group() for s in json_strings(text) for m in HOST_PATH.finditer(s)} - seen
    found.extend((str(path), host_path) for host_path in sorted(escaped))
    return [f"{where}: {host_path}" for where, host_path in found]


def scrub_text(text: str, rules: Rules, counts: Counter[str]) -> str:
    for kind, pattern in rules:
        text, n = pattern.subn(f"[redacted-{kind}]", text)
        counts[kind] += n
    return text


def scrub_plain(text: str, rules: Rules, counts: Counter[str]) -> str:
    text = scrub_text(text, rules, counts)
    if UNICODE_ESCAPE.search(text):
        raise Unchecked("a \\uXXXX escape outside JSON could hide a name")
    return text


def scrub_string(text: str, rules: Rules, counts: Counter[str]) -> str:
    """Text that may hold JSON or JSONL: decoded first when a \\uXXXX escape could hide a name."""
    if not UNICODE_ESCAPE.search(text):
        return scrub_text(text, rules, counts)
    if (scrubbed := scrub_document(text, rules, counts)) is not None:
        return scrubbed
    if "\n" in text:
        return scrub_lines(text, rules, counts)
    return scrub_plain(text, rules, counts)


def unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """json.loads keeps only the last value of a repeated key, so the others would go unchecked."""
    value = dict(pairs)
    if len(value) != len(pairs):
        raise Unchecked("a repeated JSON key hides a value from the check")
    return value


def scrub_value(value: Any, rules: Rules, counts: Counter[str]) -> Any:
    if isinstance(value, str):
        return scrub_string(value, rules, counts)
    if isinstance(value, list):
        return [scrub_value(v, rules, counts) for v in value]
    if isinstance(value, dict):
        return {
            scrub_string(k, rules, counts): scrub_value(v, rules, counts) for k, v in value.items()
        }
    return value


def scrub_document(text: str, rules: Rules, counts: Counter[str]) -> str | None:
    """One JSON document, kept byte for byte unless a string in it changed; None if not JSON."""
    try:
        value = json.loads(text, object_pairs_hook=unique_keys)
    except json.JSONDecodeError:
        return None
    before = counts.total()
    value = scrub_value(value, rules, counts)
    if counts.total() == before:
        return text
    indent = 2 if "\n" in text.strip() else None
    return json.dumps(value, indent=indent, ensure_ascii=False) + text[len(text.rstrip()) :]


def scrub_json(text: str, rules: Rules, counts: Counter[str]) -> str:
    if (scrubbed := scrub_document(text, rules, counts)) is not None:
        return scrubbed
    return scrub_plain(text, rules, counts)


def scrub_lines(text: str, rules: Rules, counts: Counter[str]) -> str:
    lines = text.split("\n")
    return "\n".join(scrub_json(line, rules, counts) if line.strip() else line for line in lines)


def scrub_file(path: Path, text: str, rules: Rules, counts: Counter[str]) -> str:
    if path.suffix == ".json":
        return scrub_json(text, rules, counts)
    if path.suffix == ".jsonl":
        return scrub_lines(text, rules, counts)
    return scrub_string(text, rules, counts)


def run(args: argparse.Namespace) -> int:
    root: Path = args.dir
    if not root.is_dir():
        print(f"scrub: {root} is not a directory", file=sys.stderr)
        return 2
    rules = build_rules(args.hostname, args.home, args.user)
    counts: Counter[str] = Counter()
    files, unchecked = walk_files(root)
    changed = 0
    left: list[str] = []
    for path in files:
        try:
            text = path.read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError) as e:
            unchecked.append(f"{path}: {e}")
            continue
        file_counts: Counter[str] = Counter()
        try:
            scrubbed = scrub_file(path, text, rules, file_counts)
        except Unchecked as e:
            unchecked.append(f"{path}: {e}")
            continue
        counts.update(file_counts)
        left.extend(host_paths(path, scrubbed))
        if scrubbed != text:
            path.write_bytes(scrubbed.encode("utf-8"))
            changed += 1
    kinds = ("key", "endpoint", "host", "ip", "home", "user")
    summary = " ".join(f"{kind}={counts[kind]}" for kind in kinds)
    print(f"scrub: changed {changed} file(s) of {len(files)}; redactions: {summary}")
    if unchecked:
        print("scrub: cannot check these; remove or scrub by hand before upload:", file=sys.stderr)
        for problem in unchecked:
            print(f"  {problem}", file=sys.stderr)
    if left:
        print(
            "scrub: host paths left; rerun with --home/--user of the machine that ran the job:",
            file=sys.stderr,
        )
        for problem in left:
            print(f"  {problem}", file=sys.stderr)
    return 1 if unchecked or left else 0
