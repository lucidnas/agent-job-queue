#!/bin/sh
# Claude Code SessionStart hook (agent-job-queue): puts the live machine queue in every new session's context.
AQ=$(command -v aq || echo "$HOME/.local/bin/aq")
S=$("$AQ" status 2>/dev/null | head -14)
python3 - "$S" <<'PY'
import json, sys
s = sys.argv[1] or "aq not available"
ctx = ("This machine shares one job queue, `aq` (agent-job-queue), across all agents. Run heavy work (renders, video encodes, "
       "local Whisper/TTS models, Blender, big builds) only via `aq run -p <project> -n <name> -w <4|2|1> -- <cmd>`, "
       "in chunks of a few minutes; one project runs at a time. Check `aq status` first. Current machine and queue:\n" + s)
print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": ctx}}))
PY
