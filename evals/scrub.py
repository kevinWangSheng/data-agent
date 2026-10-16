"""`scrub DIR`: remove API keys, the model endpoint, IPv4 addresses and hostnames before upload.

Rewrites every file under DIR in place (plan §7: published trajectories leak nothing). In `.json`
and `.jsonl` files it scrubs the decoded strings and re-serializes, so JSON escapes neither hide a
secret nor get broken by a redaction; other files, and lines that do not parse, are scrubbed as
text.
Anything it cannot check (non-UTF-8 or unreadable files, unreadable directories, symlinks) is
listed and the command fails.
"""

import argparse
import json
import os
import re
import sys
from collections import Counter
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

# (marker kind, pattern): matches become "[redacted-<kind>]".
type Rules = list[tuple[str, re.Pattern[str]]]


def add_parser(commands: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    parser = commands.add_parser("scrub", help="remove keys, endpoints, IPs and hostnames in place")
    parser.add_argument("dir", type=Path)
    parser.add_argument(
        "--hostname", action="append", default=[], help="extra hostname to remove (repeatable)"
    )
    parser.set_defaults(run=run)


def literal(value: str) -> re.Pattern[str]:
    return re.compile(re.escape(value), re.IGNORECASE)


def hostname(value: str) -> re.Pattern[str]:
    """Match a hostname as a whole label sequence, so "db" does not hit "db-1" or "duckdb"."""
    return re.compile(rf"{NAME_START}{re.escape(value)}(?![A-Za-z0-9_-])", re.IGNORECASE)


def build_rules(hostnames: list[str]) -> Rules:
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
    return rules


def scrub_text(text: str, rules: Rules, counts: Counter[str]) -> str:
    for kind, pattern in rules:
        text, n = pattern.subn(f"[redacted-{kind}]", text)
        counts[kind] += n
    return text


def scrub_value(value: Any, rules: Rules, counts: Counter[str]) -> Any:
    if isinstance(value, str):
        return scrub_text(value, rules, counts)
    if isinstance(value, list):
        return [scrub_value(v, rules, counts) for v in value]
    if isinstance(value, dict):
        return {
            scrub_text(k, rules, counts): scrub_value(v, rules, counts) for k, v in value.items()
        }
    return value


def scrub_json(text: str, rules: Rules, counts: Counter[str]) -> str:
    """One JSON document; kept byte for byte unless a string in it changed."""
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return scrub_text(text, rules, counts)
    before = counts.total()
    value = scrub_value(value, rules, counts)
    if counts.total() == before:
        return text
    indent = 2 if "\n" in text.strip() else None
    return json.dumps(value, indent=indent, ensure_ascii=False) + text[len(text.rstrip()) :]


def scrub_file(path: Path, text: str, rules: Rules, counts: Counter[str]) -> str:
    if path.suffix == ".json":
        return scrub_json(text, rules, counts)
    if path.suffix == ".jsonl":
        lines = text.split("\n")
        return "\n".join(
            scrub_json(line, rules, counts) if line.strip() else line for line in lines
        )
    return scrub_text(text, rules, counts)


def run(args: argparse.Namespace) -> int:
    root: Path = args.dir
    if not root.is_dir():
        print(f"scrub: {root} is not a directory", file=sys.stderr)
        return 2
    rules = build_rules(args.hostname)
    counts: Counter[str] = Counter()
    files, unchecked = walk_files(root)
    changed = 0
    for path in files:
        try:
            text = path.read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError) as e:
            unchecked.append(f"{path}: {e}")
            continue
        scrubbed = scrub_file(path, text, rules, counts)
        if scrubbed != text:
            path.write_bytes(scrubbed.encode("utf-8"))
            changed += 1
    summary = " ".join(f"{kind}={counts[kind]}" for kind in ("key", "endpoint", "host", "ip"))
    print(f"scrub: changed {changed} file(s) of {len(files)}; redactions: {summary}")
    if unchecked:
        print("scrub: cannot check these; remove or scrub by hand before upload:", file=sys.stderr)
        for problem in unchecked:
            print(f"  {problem}", file=sys.stderr)
        return 1
    return 0
