# agent-job-queue (`aq`)

**One machine, many AI agents, one line.** `aq` is a tiny machine-wide job queue for AI coding agents (Claude Code, Codex, Gemini CLI, Cursor, scripts). Heavy work takes turns, so your machine never gets buried.

```
$ aq status
aq 0.2.0  my-mac  10 cores  16 GB
CPU  [█████████████████▓▓·········]    68%   aq 62%  other 6%
MEM  [███▓▓▓▓▓▓▓▓·················]   6.1/16 GB   aq 1.6 GB  other 4.5 GB
LOAD 3.0 3.4 4.9   QUEUE 4/4 capacity   █ aq jobs  ▓ everything else  · free
RUNNING
  ▶ w4   0m09s  cpu  528%  mem  1.6G  [video-app] render v3 chunk 3/3
WAITING
   1 w4 waited   4m59s  [docs-site] screenshot pass
   2 w4 waited   0m10s  [podcast] voiceover 1/1
```

## Why this exists

We run a lot of agents at once on one laptop. Each makes videos: rendering frames, encoding, generating voiceovers, running Whisper.

1. **Every agent thinks it is alone.** Five agents start five renders at the same moment. The laptop's fans scream, memory swaps, everything slows to a crawl, and sometimes the machine falls over.
2. **So we paid for cloud GPUs.** We moved heavy work to a cloud provider to protect the laptop. It worked, and we burned through the spending limit on three separate accounts.
3. **The machine was never too small.** One render at a time ran fine on the laptop. Nothing was controlling how much work ran at once.
4. **So we gave the agents a traffic light.** With `aq`, every heavy command waits its turn. The same laptop now renders all day, nothing is sent to the cloud, and the cloud bill for rendering went to zero.

If you're paying for cloud compute because your agents overload a perfectly good machine, this is for you.

## Install

```bash
pipx install agent-job-queue        # or: uv tool install agent-job-queue
aq install                          # tell your agents about it (see below)
```

From source: `pipx install git+https://github.com/lucidnas/agent-job-queue`. Needs Python 3.9+ on macOS or Linux; no dependencies.

## Use

```bash
aq run -p my-project -n "render chunk 3/12" -w 4 -- node render.mjs video out.mp4 30 16 24
aq run -w 2 -- whisper audio.wav --model medium.en
aq status            # machine CPU/memory + what the queue is doing
aq status --watch    # live view, refreshes every 2 s
aq status --json     # for agents and scripts
aq capacity 4        # total capacity (default 4)
```

`aq run` waits for its turn, runs the command with the same stdin, stdout and exit code, and frees its slot when the command exits, even if it crashes.

### Rules

- **Strict first-in-first-out order:** jobs start in the order they arrive.
- **One project at a time:** a job won't start while another project's job is running. The project is the git top-level folder, or `-p`. Two projects rendering at once is exactly what overloads a machine.
- **Weights:** every job has a weight, and running jobs share a capacity of 4:

  | Weight | What it's for |
  |---|---|
  | 4 | exclusive: renders, local TTS/LLM models |
  | 2 | heavy: Whisper, long encodes |
  | 1 | light: stills, short ffmpeg jobs |

  Small jobs from the same project can run side by side.
- **Chunks:** split long work into many short `aq run` jobs of a few minutes each. Other agents' jobs can slot in between chunks, and nothing holds the machine for an hour.

## Getting agents to use it

Agents are the main users, so `aq` tries to work out of the box:

```bash
aq install          # = aq install claude rules
```

- **Claude Code:** adds a `SessionStart` hook to `~/.claude/settings.json` and backs up the old file first. Every new session starts with the live `aq status` and the rule in its context.
- **rules:** writes a short marked rule block into the instruction files agents read: `~/AGENTS.md` (if present), `~/.claude/CLAUDE.md`, `~/.codex/AGENTS.md` (Codex) and `~/.gemini/GEMINI.md` (Gemini CLI). Re-running updates the block in place.

**Opt-in: blocking.**

```bash
aq install claude --block
```

This adds a `PreToolUse` hook that stops heavy commands run outside `aq` and tells the agent the exact `aq run …` line to use instead. It matches renders, video encodes, local Whisper/TTS and Blender.

It skips:
- quick ffmpeg work (single frames, stream copy);
- commit messages and heredoc bodies;
- read-only commands.

Mark a deliberate one-off with `# aq-skip`. Add your own patterns in `~/.agent-job-queue/config.json`:

```json
{"capacity": 4,
 "hook_allow": ["tools/my_wrapper\\.py"],
 "hook_heavy": [["cargo build --release", "a release build"]]}
```

Undo everything with `aq install --uninstall claude`.

## How it works

- **Job files:** each job is a small JSON file in `~/.agent-job-queue/jobs/` (or `$AQ_HOME`), guarded by an `flock`. No server, no database, no daemon.
- **Dead jobs:** a job whose `aq` process has died is dropped the next time anyone reads the queue.
- **Usage view:** `aq status` reads `ps`, `vm_stat` (macOS) or `/proc/meminfo` (Linux). It adds up the CPU and memory of each running job's whole process tree, which is how it splits usage into "aq jobs" and "everything else".

## Prior art

- **[block/agent-task-queue](https://github.com/block/agent-task-queue)** solves the same problem with an MCP-first design: agents call a `run_task` tool, with named and hierarchical queues, bounded progress updates and a desktop sidecar. If your agents live in MCP, look at it.
- **agent-job-queue** is CLI-first instead:
  - wrap any command, so any agent, script or cron job can use it;
  - one project at a time with weights;
  - live machine CPU and memory split between queued work and everything else;
  - agents join automatically through session-start context, rule files and optional blocking.
- **General queues:** [pueue](https://github.com/Nukesor/pueue), [task-spooler](https://viric.name/soft/ts/), GNU parallel's `sem` and [nq](https://github.com/leahneukirchen/nq) are good general-purpose queues, but don't know about agents or projects.
- **Different tool:** [RQ (Redis Queue)](https://python-rq.org) is a Python job library, unrelated. That's why our command is `aq`, not `rq`.

## Roadmap

- **Opt-in watchdog** (`aq watch warn|pause`): spots heavy processes running outside the queue by measuring them, and counts them as load.
- **ETAs:** from job history, so you can see when a job will start and finish.
- **Emergency priority** (`--priority urgent`), plus pause, resume and cancel.
- **Shared event feed** (`aq watch`, `aq say`), so agents can see and post what's happening.
- **Configurable resources and profiles:** gpu, ram, API rate limits.
- **Stand-in commands on PATH** for `ffmpeg`, `whisper` and others, and an MCP server.

## License

MIT. See [LICENSE](LICENSE).
