#!/usr/bin/env python3
"""Claude Code PreToolUse hook (matcher: Edit|Write|NotebookEdit|Bash). Opt-in: `aq install claude --focus`.
Blocks edits (and shell commands run inside) a registered project that is not the focus, unless the focus is waiting.
Reads, `aq` commands, unregistered paths and anything it can't parse are always allowed."""
import json, re, sys
from pathlib import Path

try:
    ev = json.load(sys.stdin)
    tool, ti = ev.get("tool_name", ""), ev.get("tool_input", {}) or {}
except Exception:
    sys.exit(0)
if tool == "Bash":
    cmd = ti.get("command", "") or ""
    if re.search(r"\b(aq|agent-job-queue)\b", cmd): sys.exit(0)
    # read-only commands never get blocked: every part is a known reader and nothing is redirected into a file
    READ = r"\s*(ls|cat|head|tail|less|wc|grep|rg|find|stat|du|df|file|pwd|which|echo|tree|ffprobe|git\s+(status|log|diff|show|branch|remote|rev-parse|ls-files|blame))\b"
    if ">" not in cmd and all(re.match(READ, part) for part in re.split(r"&&|\|\||;|\|", cmd) if part.strip()): sys.exit(0)
    target = ev.get("cwd")
else:
    target = ti.get("file_path") or ti.get("notebook_path")
if not target:
    sys.exit(0)
try:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from agent_job_queue import projects
    focus = projects.get_focus(); proj = projects.match_path(target)
except Exception:
    sys.exit(0)
if not proj or not focus["project"] or proj == focus["project"] or focus["waiting_on"] or proj == "inbox":
    sys.exit(0)
msg = (f'The focus project is {focus["project"]}, and this touches {proj}. Don\'t work on {proj} now: add it with '
       f'`aq idea -p {proj} "<what>"`. If the focus is waiting on something, run `aq wait "<what>"` first. '
       f'Only if the owner said so: `aq focus {proj} --by owner`.')
print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": msg}}))
