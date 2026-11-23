#!/usr/bin/env python3
"""Claude Code PreToolUse hook (matcher: Bash). Blocks heavy local commands that are not run through `aq run` (opt-in: `aq install claude --block`).
Heavy = video renders/encodes, local Whisper, local TTS, Blender. Quick ffmpeg work (probe, single frames, stream copy) passes.
Escape hatch for a one-off: put `# aq-skip` in the command."""
import json, re, sys
try:
    cmd = json.load(sys.stdin).get("tool_input", {}).get("command", "") or ""
except Exception:
    sys.exit(0)
if not cmd or "aq-skip" in cmd or re.search(r"\b(aq|agent-job-queue)\s+run\b", cmd):
    sys.exit(0)
# ignore text that is data, not a command: heredoc bodies, quoted strings, and read-only/git/echo lines
cmd = re.sub(r"<<-?\s*['\"]?(\w+)['\"]?.*?\n.*?\n\1\b", "", cmd, flags=re.S)
cmd = re.sub(r"'[^']*'|\"[^\"]*\"", "''", cmd)
cmd = "\n".join(l for l in re.split(r"\n|&&|;|\|\|", cmd)
                if not re.match(r"\s*(git|grep|rg|cat|echo|printf|sed|awk|ls|head|tail|find|which|man|aq|agent-job-queue)\b", l))
# extra allow/heavy patterns from $AQ_HOME/config.json: {"hook_allow": [regex...], "hook_heavy": [[regex, "label"], ...]}
import os
from pathlib import Path
try:
    _cfg = json.loads((Path(os.environ.get("AQ_HOME", Path.home() / ".agent-job-queue")) / "config.json").read_text())
except Exception:
    _cfg = {}
if any(re.search(p, cmd) for p in _cfg.get("hook_allow", [])):
    sys.exit(0)
HEAVY = [tuple(x) for x in _cfg.get("hook_heavy", [])] + [
    (r"render\.mjs\s+video\b", "a video render (render.mjs video)"),
    (r"\bhyperframes\s+render\b|\bnpx\s+hyperframes\s+render\b", "a HyperFrames render"),
    (r"\bblender\b.*\s-b\b|\bblender\s+--background\b", "a Blender render"),
    (r"\bwhisper(-cli)?\s|\bmlx_whisper\b|import whisper|faster_whisper", "local Whisper"),
    (r"\bchatterbox\b|ChatterboxTTS", "local TTS (Chatterbox)"),
]
hit = next((why for pat, why in HEAVY if re.search(pat, cmd)), None)
if not hit and re.search(r"\bffmpeg\b", cmd):
    encodes = re.search(r"libx26[45]|libvpx|-c:v\s+(?!copy)|-vcodec\s+(?!copy)|\bh264_videotoolbox\b", cmd)
    quick = re.search(r"-frames:v\s+\d|-vframes\s+\d|-c\s+copy|-c:v\s+copy", cmd)
    if encodes and not quick:
        hit = "an ffmpeg video encode"
if not hit:
    sys.exit(0)
msg = (f"This looks like {hit}, which must go through the machine queue `aq` (agent-job-queue: one project at a time, work in chunks). "
       f"Run `aq status` first, then wrap it: `aq run -p <project> -n \"<what>\" -w <4 render/TTS | 2 whisper/encode | 1 light> -- <command>`. "
       "Split long jobs into chunks of a few minutes. If this really is a tiny job, add `# aq-skip` to the command.")
print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": msg}}))
