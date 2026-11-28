# Changelog

## 0.3.1

- Focus guard checks every path a shell command would write to, not only its folder.
- Switching the focus project asks the owner to approve (agents can't switch themselves).
- `aq idea -p inbox` works before the inbox exists.

## 0.3.0 (2026-11-26)
`aq` becomes the control plane: one focus project, nothing lost.

- Projects: `aq project add|ls|show|state|scan`; `aq run` names jobs from registered paths.
- Focus: `aq focus`, `aq wait`, `aq resume`, `aq next`; ideas go into project inboxes with `aq idea`.
- `aq board`: focus, queue, ideas, guard warnings and backup status; also shown at Claude Code session start.
- `aq guard`: git repo, uncommitted/unpushed, media on the Mac, low disk; `--fix` makes a private GitHub repo.
- `aq backup` + `aq install backup`: hourly move of old Claude sessions to the SSD and `rclone copy` of the SSD off-site.
- `aq history restore`. Opt-in focus guard hook: `aq install claude --focus`.

## 0.2.1 (2026-11-22)
- `aq install` runs the hooks through `sh` and the current Python, so they work even if a package install drops the executable bit.
- Leaves an existing queue section mentioning `aq` or `rq` in an instruction file alone.
- Install from GitHub (`pipx install git+https://github.com/lucidnas/agent-job-queue`); not on PyPI yet.

## 0.2.0 (2026-11-22)
First public release.

- `aq run`: FIFO queue with weights, a capacity limit and one project at a time.
- `aq status`: machine CPU and memory bars split into aq jobs vs everything else, per-job CPU/memory, running and waiting jobs. Also `--watch` (live view) and `--json`.
- `aq install`: Claude Code session-start hook, plus rule blocks for Claude, Codex and Gemini. Optional `--block` adds a PreToolUse hook that stops heavy commands run outside aq.
- `aq capacity`, `aq --version`.

## 0.1.0 (2026-11-22)
Internal version, used on one machine by about ten agent sessions to share video renders, voice generation and Whisper.
