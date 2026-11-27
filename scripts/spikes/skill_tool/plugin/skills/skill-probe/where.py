"""Ticket 142 spike: run in a sandbox, it shows the sandbox reached this Skill's script."""

from pathlib import Path

print(f"WHERE-142 ran {Path(__file__).resolve()}")
