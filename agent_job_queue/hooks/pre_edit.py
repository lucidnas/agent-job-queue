#!/usr/bin/env python3
"""Claude Code PreToolUse hook (matcher: Edit|Write|NotebookEdit|Bash). Opt-in: `aq install claude --focus`.

1. Blocks edits to a registered project that is not the focus, unless the focus is waiting. For shell commands it
   looks at the folder the command runs in, `cd` targets and every path written in the command (for cp/mv-style
   commands only the destination counts, so copying *from* another project into the focus is fine).
2. Switching the focus project (`aq focus <name>`, or touching focus.json) is blocked for agents in every permission
   mode; the owner runs it themselves (`!aq focus <name> --by owner` in the message box, or a terminal). `aq wait` / `aq resume` don't switch projects and are not asked about.
Reads, unregistered paths and anything it can't parse are allowed."""
import json, os, re, shlex, sys
from pathlib import Path

try:
    ev = json.load(sys.stdin)
    tool, ti = ev.get("tool_name", ""), ev.get("tool_input", {}) or {}
except Exception:
    sys.exit(0)
READER = re.compile(r"(ls|cat|head|tail|less|more|wc|grep|egrep|rg|find|stat|du|df|file|pwd|which|type|echo|printf|tree|jq|ffprobe|"
                    r"sed\s+-n|md5|shasum|sort|uniq|cut|tr|diff|true|"
                    r"git(\s+-C\s+\S+)*\s+(status|log|diff|show|branch|remote|rev-parse|rev-list|ls-files|blame|describe|tag\s*$|config\s+--get))\b")
FOCUS_SWITCH = re.compile(r"(?:\baq|\brq|\bagent-job-queue|agent_job_queue)\s+focus\s+(?!-)([A-Za-z0-9][A-Za-z0-9._-]*)")
COPY_LIKE = {"cp", "rsync", "ln", "install", "scp", "ditto"}
SEPS = {"&&", "||", ";", "|", "&", "(", ")", ";;", "|&"}
REDIRS = {">", ">>", "&>", ">|", "&>>"}


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


def _pathy(t): return t.startswith(("/", "~", "./", "../")) or t in (".", "..")


def _flush(part, here):
    """(paths this one command may write, folder after it)."""
    redir = [part[i + 1] for i, t in enumerate(part[:-1]) if t in REDIRS]
    words = [t for i, t in enumerate(part) if t not in REDIRS and not (i and part[i - 1] in REDIRS)]
    while words and re.match(r"^\w+=", words[0]): words = words[1:]      # leading VAR=x
    res = [r for r in redir if r != "/dev/null" and not r.startswith("&")]
    if not words: return res, here
    if words[0] == "cd":
        return res, (os.path.join(here, os.path.expanduser(words[1])) if len(words) > 1 else here)
    if READER.match(" ".join(words)) or words[0] in ("aq", "rq", "agent-job-queue"):
        return res, here
    for i, t in enumerate(words):                                       # git -C <dir>, make -C <dir>
        if t == "-C" and i + 1 < len(words): res.append(words[i + 1])
    paths = [a.split("=", 1)[-1] for a in words[1:] if not a.startswith("-") and _pathy(a.split("=", 1)[-1])]
    if words[0] in COPY_LIKE: paths = paths[-1:]
    return res + paths, here


def write_targets(cmd, cwd):
    """Paths a shell command may write to: its folder, cd targets, redirect targets and path arguments."""
    try:
        lex = shlex.shlex(cmd.replace("\n", " ; "), posix=True, punctuation_chars=True); lex.whitespace_split = True
        toks = list(lex)
    except ValueError:
        return [cwd]
    here, out, part = cwd, [cwd], []
    for t in toks + [";"]:
        if t in SEPS:
            if part:
                res, here = _flush(part, here)
                out += [os.path.join(here, os.path.expanduser(p)) for p in res] + [here]
            part = []
        else:
            part.append(t)
    return out


OWNER_ONLY = ("Only the owner switches the focus project, and agents can't do it in any permission mode. "
              "Tell the owner which project and why, and ask them to type `!aq focus <project> --by owner` in the message box "
              "or run it in a terminal. If the focus is only waiting on something, use `aq wait \"<what>\"` instead.")


def block(msg):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                             "permissionDecisionReason": msg + " " + OWNER_ONLY}}))
    sys.exit(0)


try:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from agent_job_queue import projects
    focus = projects.get_focus()
except Exception:
    sys.exit(0)
cwd = ev.get("cwd") or os.getcwd()
focus_file = os.path.realpath(str(projects.FOCUS))

# --- 2. only the owner switches the focus project ---
if tool == "Bash":
    cmd = ti.get("command", "") or ""
    m = FOCUS_SWITCH.search(cmd)
    if m and m.group(1) != focus["project"]:
        block(f'This would switch the focus project from {focus["project"] or "none"} to {m.group(1)}.')
    if "focus.json" in cmd and not read_only(cmd):
        block("This command touches aq's focus.json.")
    if read_only(cmd): sys.exit(0)
    targets = write_targets(cmd, cwd)
else:
    t = ti.get("file_path") or ti.get("notebook_path")
    if not t: sys.exit(0)
    if os.path.realpath(os.path.expanduser(t)) == focus_file:
        block("This edits aq's focus.json.")
    targets = [t]

# --- 1. edits stay in the focus project ---
if not focus["project"] or focus["waiting_on"]:
    sys.exit(0)
try:
    hit = next((p for p in (projects.match_path(x) for x in targets) if p and p not in (focus["project"], "inbox")), None)
except Exception:
    sys.exit(0)
if not hit:
    sys.exit(0)
msg = (f'The focus project is {focus["project"]}, and this touches {hit}. Don\'t work on {hit} now: add it with '
       f'`aq idea -p {hit} "<what>"`. If the focus is waiting on something, run `aq wait "<what>"` first. '
       f'To switch projects, ask the owner to type `!aq focus {hit} --by owner`.')
print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": msg}}))
