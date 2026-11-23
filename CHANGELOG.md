# Changelog

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
