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
READER = re.compile(r"(ls|cat|head|tail|less|more|wc|grep|egrep|rg|find|stat|du|df|file|pwd|which|type|echo|printf|tree|jq|ffprobe|"
                    r"sed\s+-n|md5|shasum|sort|uniq|cut|tr|diff|true|"
                    r"git(\s+-C\s+\S+)*\s+(status|log|diff|show|branch|remote|rev-parse|rev-list|ls-files|blame|describe|tag\s*$|config\s+--get))\b")


def read_only(cmd):
    """True when every part of the command only reads (or is an aq command / a cd), and nothing writes to a file."""
    c = re.sub(r"'[^']*'|\"[^\"]*\"", "''", cmd)                       # quoted text is data
    c = re.sub(r"\d?>\s*/dev/null|\d?>&\d|&>\s*/dev/null", " ", c)     # harmless redirects
    if ">" in c or re.search(r"\bsed\s+(-\w*\s+)*-i|\bfind\b.*\s-(delete|exec)\b", c): return False
    for part in re.split(r"&&|\|\||;|\||\n", c):
        part = re.sub(r"^\s*(\w+=\S*\s+)*", "", part).strip()          # leading VAR=x
        if not part or re.match(r"(cd|aq|agent-job-queue)\b", part) or READER.match(part): continue
        return False
    return True


if tool == "Bash":
    if read_only(ti.get("command", "") or ""): sys.exit(0)
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
