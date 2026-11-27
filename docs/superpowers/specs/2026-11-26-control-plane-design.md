# aq as a control plane: one focus project, nothing lost

Status: draft for owner approval (2026-11-26)

## Why

The owner runs many Claude sessions across several projects at once: videos, apps and research. Ideas keep arriving, each one becomes a new chat, and there are now 51 sessions that nobody can keep up with. Work also sits in places that can fail:
- the Mac's disk filled up, with 18 GB of Claude history, and blocked all work;
- the SSD sometimes isn't detected;
- some code isn't in git.

`aq` already stops agents from overloading the machine by taking heavy jobs in turns, one project at a time. The goal is to grow it into the one place that decides how work runs:
- **one focus project at a time;**
- **new ideas get queued instead of started;**
- **nothing is ever only on one disk.**

The owner wants to open source it later, as `aq` already is.

## Goals

1. **Focus.** One project is the focus. Claude works on it; ideas and requests for other projects get written into those projects' queues. Claude may switch only while the focus is *waiting* on something it can't speed up, like research agents, a render, the owner's answer or a YouTube sign-in. When the wait ends, it switches back.
2. **One main chat.** The owner talks to one long-lived session, including over Remote Control from the phone. It does work through subagents and linked sessions, which report back to it. `aq board` is what it reads to know where everything stands.
3. **Everything in git.** Every project folder that holds code is a git repo with a GitHub remote, and changes get pushed to a branch or upstream. `aq` reports any project that has no repo, has uncommitted changes or has unpushed commits.
4. **Assets on the SSD, never on the Mac.** `aq` flags media files found in project folders on the Mac.
5. **Hourly off-site copy.** A background job runs every hour and copies the SSD's working folders (assets, renders, VO, and Claude history) to Google Drive. If the SSD is missing, it says so loudly and does nothing destructive.
6. **History off the Mac.** Claude session history older than 7 days moves to the SSD every hour, so the Mac never fills up again. The current week stays on the Mac, so live sessions never depend on the SSD.

## Non-goals

- Running several coding agents in parallel on separate git branches. That's what Conductor, Claude Squad and similar tools do, and it would make the sprawl worse.
- A web app or a hosted service. This stays a local command-line tool plus files.
- Replacing the Claude desktop app's own session features. `aq` sits next to them.

## Anti-goals: it fails if…

- it blocks the owner. The owner can always override the focus with one command: `aq focus <project>`;
- it ever deletes the only copy of anything. Every move is copy, check, then remove;
- live Claude sessions depend on the SSD being present;
- it needs a server, a database service or any pip dependency. That would break "install in one line" and open-sourcing.

## Constraints

- **Hardware:** macOS, an M4 with 16 GB of memory and a 228 GB disk that was full. The SSD is 1.8 TB with 34 GB free on Nov 26, so the SSD is nearly full too.
- **Code:** `aq` is Python 3.9+ with no dependencies. It keeps its state as JSON files with a file lock (`~/.aq/jobs`, `state.lock`). It installs a Claude Code `SessionStart` hook and an optional blocking hook.
- **Google Drive:** Drive for Desktop is **not** installed on this Mac. Instead, `rclone` is installed with a Google Drive remote (name set in config), which has 5 TiB total and 4.49 TiB free. The backup goes through rclone, so nothing goes through a local sync folder and the Mac never fills up. That remote uses rclone's shared client ID, which Google is retiring in 2026, so the owner needs to make their own client ID once (a step for the owner).
- **Claude history:** it lives in `~/.claude/projects`. Claude Code's sandbox won't let its own agents delete files there, so the move step runs as a launchd job owned by the user, not from inside a Claude session.

## Approaches considered

**A. Extend `aq` (picked).** Add projects, focus, ideas, a guard and backup to the existing tool. It keeps the same storage style (JSON files plus a lock), installer and hooks.
- Pros: one tool to install, fits what agents already use, no dependencies, easy to open source.
- Con: `aq` grows from about 400 lines to about 1,500.

**B. Adopt an existing orchestrator** (Orc, ORCH, Claude Task Master, Claude Code agent teams). None of these has "one focus project" or the backup and git guarantees. They're built for coding tasks, not for a person juggling video and app projects. We'd still have to build the guard and backup ourselves.

**C. A separate new tool** (say, a `pq` project queue) alongside `aq`. It's cleaner to explain, but it would be a second install and a second hook. The owner asked for `aq` to *be* the control plane.

**Why A:** it matches the owner's words ("AQ becomes really the control plane"), it reuses the hooks and installer agents already follow, and it stays a single dependency-free tool.

**Storage, my call:** JSON files plus the lock, the same as jobs. SQLite is in Python's standard library and would work too, but JSON keeps one style, and both people and agents can read and fix it by hand. Revisit if a single file grows past a few MB.

## Design

### 1. Projects and focus

`~/.aq/projects/<name>.json` holds:
- the name, the path(s), the GitHub remote, a one-line goal and a priority;
- the state: `active`, `queued`, `parked` or `done`;
- an ordered `inbox` of ideas and requests, each with a timestamp and who added it;
- `last_touched`.

The focus is a separate pointer in `~/.aq/focus.json`: `{project, waiting_on, since, set_by}`. When `waiting_on` is set, the focus is waiting. While it waits, Claude may work on any queued project, and `aq resume` or the wait clearing brings it back.

Each inbox item records who added it: `--by` text, or else `$CLAUDE_SESSION_ID`, or else `owner`. That's for the record, not for security.

Project matching: registered paths replace the git-top-level guess in `aq run`/`aq status`. The longest matching registered path wins, and anything unregistered falls back to today's behaviour. `aq project scan ~/code "/Volumes/SSK SSD"` suggests projects to register; registering stays a manual step.

Commands:
- `aq project add <name> --path … --goal "…"`; `aq project ls`
- `aq focus <name>` switches the focus; the old focus goes back to `queued`.
- `aq wait "<what>"` marks the focus as waiting, which allows a switch. `aq resume` clears the wait.
- `aq idea "<text>" [-p project]` adds to a project's inbox. With no project it goes to an `inbox` project that the main chat sorts later.
- `aq next` suggests what to do next: the focus if it isn't waiting, otherwise the highest-priority queued project with inbox items.
- `aq board` (and `--json`) shows the focus, what's waiting and on what, the queue, and the guard warnings from section 3.

**Rule for small requests to another project (my call, the owner can change it):** they go into that project's inbox. Claude doesn't do them right away unless the focus is waiting. The owner can always say "do it now", which is an explicit override, and that gets logged.

### 2. Enforcing focus in Claude Code

- The `SessionStart` hook (it exists already) also prints `aq board` and the focus rule, so every session knows the focus.
- An opt-in `PreToolUse` hook, like `--block` today:
  - It checks Edit, Write and NotebookEdit paths, plus Bash commands whose working folder is inside a registered project. It doesn't try to parse what a shell command writes.
  - When the target is a registered project other than the focus, and the focus isn't waiting, it blocks with the message "focus is X; add this with `aq idea` or `aq focus`".
  - Reads, research, `aq` commands and anything outside registered projects (home, scratch folders) are never blocked.
- `aq focus` can be run by agents, but only when the owner said so. The rule says this, every switch is logged with `set_by`, and `aq board` shows the last switch. The point is to make drift visible, not to lock the owner out.
- A rules block in `CLAUDE.md`/`AGENTS.md`, written by `aq install`, explains the workflow to agents.

### 3. Guard: git, assets and disk

`aq guard`, which runs hourly and also inside `aq board`, checks every registered project for:
- **no git repo:** warn. With `--fix`, run `git init`, add a `.gitignore` and run `gh repo create --private` under the owner's personal GitHub account (the one `gh` is signed in to);
- **uncommitted or unpushed changes:** warn only, since committing is the owner's or agent's job. `aq board` lists them. An agent finishing a task runs `aq guard -p <project>` and pushes;
- **media files over 20 MB on the Mac** inside a project folder: warn and suggest moving them to the SSD;
- **disk:** Mac free space below 15 GB or SSD free below 50 GB raises a warning on the board.

The guard never commits, pushes or deletes on its own.

### 4. Backup and history (launchd, hourly)

`aq backup`, installed as a user LaunchAgent by `aq install backup`:
1. If the SSD isn't mounted, write `SSD MISSING` to the board, send a macOS notification and stop.
2. **History:** move whole sessions (each `<id>.jsonl` plus its `<id>/` folder of subagents and tool results), using the `.jsonl` modified time, older than 7 days, to `<SSD>/claude-history/projects/…`. `memory/` is excluded. Each file is copied, its size checked, then removed from the Mac. `aq history restore <session-id>` copies a session back when the owner wants to resume an old chat.
3. **Drive:** `rclone copy` of the configured SSD folders to `<remote>:aq-backup/…`. It adds and updates files only and never deletes anything on Drive, so a missing or wiped SSD can't wipe Drive. Each run is capped by time (`--max-duration 50m`) and bandwidth so it stays within the hour. The first full upload takes days and resumes on its own.
4. Write a log line and the last-success time, which `aq board` shows.

The list of folders to copy goes in `~/.aq/config.json` (`backup.paths`, `backup.remote`).

The LaunchAgent runs as the owner. If macOS denies it access to `~/.claude` or the SSD, `aq install backup` says which app needs Full Disk Access.

Drive has room for the whole SSD: about 1.77 TB used, against 4.49 TiB free. **Default (my call): back up the whole SSD**, leaving out `tmp/`, `work/` and cache folders and macOS `._*` files. The SSD's `claude-history/` folder goes first, then the newest project folders.

### 5. One main chat (how it's used, not code)

- The owner pins one main session.
- **Quick work** runs in subagents, which report back to it.
- **Long work** runs in linked sessions, which notify it when done.
- The main chat runs `aq board` at the start of each turn.

Cleaning up the 51 old chats is a one-time job:
1. A subagent reads each session's recent turns and writes its unfinished work as `aq idea` items under the right project.
2. The sessions are then **archived** (hidden, recoverable), not deleted.

## Left to the builder

- Exact command flags and output layout, following `aq status`'s current style.
- How the hook matches a file path to a project (the longest matching registered path wins).
- Tests in `tests/test_aq.py`, following the existing pattern.
- README sections and a CHANGELOG entry.

## Build order

1. Projects, focus, ideas and `aq board`, plus the `SessionStart` output.
2. The guard: git, assets and disk.
3. `aq backup`: history to the SSD, the SSD to Drive, and the LaunchAgent.
4. The focus-blocking hook (opt-in).
5. The one-time sweep of the old chats.
