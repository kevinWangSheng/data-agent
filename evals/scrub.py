"""`scrub DIR`: remove API keys, the model endpoint, IPv4 addresses and hostnames before upload.

Rewrites every file under DIR in place (plan §7: published trajectories leak nothing). Works on the
raw text, so JSON and JSONL stay valid: no redaction marker contains a quote or a backslash.
Anything it cannot check (non-UTF-8 or unreadable files, unreadable directories, symlinks) is
listed and the command fails.
"""

import argparse
import os
import re
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

from evals.walk import walk_files

# Not preceded by a name character, except as the last character of a JSON escape: in the raw text
# of {"output": "ok\nbuild-box-7"} the hostname follows the "n" of "\n".
NAME_START = r"(?:(?<![A-Za-z0-9_-])|(?<=\\[bfnrt])|(?<=\\u[0-9A-Fa-f]{4}))"
# DeepSeek, OpenAI and Anthropic keys start with sk-; 20+ characters keeps "task-sk-x" prose out.
KEY_SHAPE = re.compile(rf"{NAME_START}sk-[A-Za-z0-9_-]{{20,}}")
OCTET = r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
IPV4 = re.compile(rf"(?<![\d.]){OCTET}(?:\.{OCTET}){{3}}(?!\.?\d)")


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


def build_rules(hostnames: list[str]) -> list[tuple[str, re.Pattern[str]]]:
    """(marker kind, pattern) in apply order: keys first, a whole endpoint before its host."""
    rules: list[tuple[str, re.Pattern[str]]] = []
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


def scrub_text(text: str, rules: list[tuple[str, re.Pattern[str]]], counts: Counter[str]) -> str:
    for kind, pattern in rules:
        text, n = pattern.subn(f"[redacted-{kind}]", text)
        counts[kind] += n
    return text


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
        scrubbed = scrub_text(text, rules, counts)
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
