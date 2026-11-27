---
name: skill-probe
description: Spike probe. Load this Skill when asked to run the skill probe.
---

# Skill probe

The canary for this Skill is CANARY-142-plum-orbit.

Reply with exactly these three lines, copied character for character:

CANARY: CANARY-142-plum-orbit
SKILL_DIR: ${CLAUDE_SKILL_DIR}
PLUGIN_ROOT: ${CLAUDE_PLUGIN_ROOT}

The probe script is `python3 "${CLAUDE_SKILL_DIR}/where.py"`.
