# aq control plane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Grow `aq` from a job queue into the control plane: registered projects with one focus, idea inboxes, a board, a git/asset/disk guard, an hourly backup (history to SSD, SSD to Drive) and an opt-in focus hook.

**Architecture:** Keep `cli.py` as the entry point and job queue. Add one stdlib module per responsibility (`projects.py`, `guard.py`, `backup.py`) plus one hook script (`hooks/pre_edit.py`). All state is JSON under `$AQ_HOME` (default `~/.agent-job-queue`, the existing home; the spec's `~/.aq` means this folder), written under the existing `locked()` flock.

**Tech Stack:** Python 3.9+, stdlib only; pytest; external CLIs called by subprocess only when present: `git`, `gh`, `rclone`, `launchctl`, `osascript`.

**Spec:** `docs/superpowers/specs/2026-11-26-control-plane-design.md`

## Global Constraints

- Python 3.9+, **no pip dependencies** (AGENTS.md: "stdlib only, so keep it that way").
- The repo is public: no personal values in code, tests or docs. Drive remote, SSD path, backup paths and projects come from `$AQ_HOME/config.json` or flags.
- Never delete the only copy: every move is copy, size-check, then remove.
- Drive copy uses `rclone copy` (never `sync`, never `--delete*`), `--max-duration 50m`.
- The guard never commits, pushes or deletes. `--fix` only runs `git init`, writes `.gitignore`, runs `gh repo create <name> --private --source <path>`.
- Hooks never block reads, `aq` commands, or paths outside registered projects; unparsable hook input exits 0 (allow).
- Thresholds: media file > 20 MB on the Mac; Mac free < 15 GB; SSD free < 50 GB; history older than 7 days.
- Work on branch `control-plane`; push the branch, never straight to `main`.
- Tests set `AQ_HOME` and `HOME` to tmp dirs (existing pattern in `tests/test_aq.py`).

## Review Focus

1. **Paths with spaces** (`/Volumes/SSK SSD/...`): project paths, backup paths and the LaunchAgent plist must survive them. Test in Task 1 and Task 6.
2. **SSD unmounted or unplugged mid-move:** history move must leave the Mac copy whenever the SSD copy is missing or a different size. Test in Task 5.
3. **A session still in use:** a `.jsonl` modified within 7 days, even if its `<id>/` folder is older, is never moved; a folder is moved only with its `.jsonl`. Test in Task 5.
4. **Path matching edge cases:** trailing slash, symlinked path, `/a/foo` must not match `/a/foobar`; nested registered paths pick the longest. Test in Task 1.
5. **Missing tools** (`rclone`, `gh`, `git` remote absent): commands report and carry on, never traceback. Test in Task 4 and Task 6.

---

### Task 1: Project registry and path matching

**Files:**
- Create: `agent_job_queue/projects.py`
- Modify: `agent_job_queue/cli.py` (`project_of`, argparse: `project` subcommand)
- Test: `tests/test_projects.py`

**Interfaces:**
- Produces: `PROJECTS = HOME/"projects"`; `load(name) -> dict | None`; `save_project(p: dict) -> None`; `all_projects() -> list[dict]`; `match_path(path: str) -> str | None` (project name, longest registered prefix, `os.path.realpath` on both sides, component-boundary match); `add(name, paths: list[str], goal="", priority=0, remote=None) -> dict`.
- Project dict: `{"name", "paths": [...], "remote", "goal", "priority": int, "state": "queued"|"active"|"parked"|"done", "inbox": [], "last_touched": float}`.
- `cli.project_of(cwd)` returns `match_path(cwd)` if not None, else the old git-top-level name.

- [ ] **Step 1: Write failing tests** in `tests/test_projects.py` (run the CLI as a subprocess like `test_aq.py`, plus direct imports with `AQ_HOME` set via `monkeypatch.setenv` and `importlib.reload`):
  - `test_add_and_ls`: `aq project add vid --path "<tmp>/SSK SSD/vid" --goal "ship"` then `aq project ls --json` lists `name == "vid"`, `state == "queued"`, the path verbatim.
  - `test_match_longest_and_boundary`: register `a` at `<tmp>/x`, `b` at `<tmp>/x/y`; `match_path("<tmp>/x/y/z") == "b"`, `match_path("<tmp>/x/q") == "a"`, `match_path("<tmp>/xy") is None`, `match_path("<tmp>/x/") == "a"`.
  - `test_match_symlink`: symlink `<tmp>/link -> <tmp>/x`; `match_path("<tmp>/link/f") == "a"`.
  - `test_run_uses_registered_project`: `aq run -- python -c pass` from cwd `<tmp>/x/y` while another job is absent: assert via `aq status --json` during a sleeping job that `project == "b"`.
- [ ] **Step 2:** `python3 -m pytest tests/test_projects.py -q` → FAIL (module missing).
- [ ] **Step 3:** Implement `projects.py` and the `aq project add|ls [--json]|scan <dirs...>` subcommands. `scan` prints git repos (dirs containing `.git`, depth ≤ 2) not yet registered, one `aq project add` suggestion per line; it never registers.
- [ ] **Step 4:** `python3 -m pytest -q` → all PASS (old tests too).
- [ ] **Step 5:** `git add -A agent_job_queue tests && git commit -m "projects: registry, path matching, aq project add/ls/scan"`

### Task 2: Focus, wait, resume, ideas, next

**Files:**
- Modify: `agent_job_queue/projects.py`, `agent_job_queue/cli.py`
- Test: `tests/test_projects.py`

**Interfaces:**
- Consumes: Task 1 functions.
- Produces: `FOCUS = HOME/"focus.json"`; `get_focus() -> dict` (`{"project": str|None, "waiting_on": str|None, "since": float, "set_by": str}`, empty-safe); `set_focus(name, by) -> dict` (old focus → `queued`, new → `active`, appends to `HOME/"focus.log"` as JSON line `{t, from, to, by}`); `wait(what) / resume()`; `add_idea(text, project=None, by=None) -> dict` (no project → project `inbox`, auto-created); `next_up() -> dict` (`{"project", "why"}`); `who() -> str` = `--by` else `$CLAUDE_SESSION_ID` else `"owner"`.
- Unknown project name → exit 2 with `aq: no project "<x>" (aq project ls)`.

- [ ] **Step 1: Failing tests:**
  - `test_focus_switch_logs`: add `a`,`b`; `aq focus a`; `aq focus b --by s1`; `load("a")["state"] == "queued"`, `load("b")["state"] == "active"`, last `focus.log` line has `from == "a", to == "b", by == "s1"`.
  - `test_wait_resume`: `aq wait "render"` → `get_focus()["waiting_on"] == "render"`; `aq resume` → `None`.
  - `test_idea_default_inbox`: `aq idea "try stickman"` → project `inbox` has 1 item with `text`, `t`, `by == "owner"` (unset `CLAUDE_SESSION_ID`).
  - `test_next`: focus `a` not waiting → `next_up()["project"] == "a"`; waiting → highest-priority queued project with inbox items (`b` priority 5 with 1 idea beats `c` priority 1 with 1 idea; a project with empty inbox is skipped); none → focus.
  - `test_focus_unknown`: `aq focus nope` exits 2.
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** Implement; CLI: `aq focus [name] [--by X]` (no name prints focus), `aq wait "<what>"`, `aq resume`, `aq idea "<text>" [-p P] [--by X]`, `aq next [--json]`. All writes inside `locked()`.
- [ ] **Step 4:** `python3 -m pytest -q` → PASS.
- [ ] **Step 5:** commit `"projects: focus, wait/resume, ideas, next"`.

### Task 3: `aq board` and SessionStart output

**Files:**
- Modify: `agent_job_queue/projects.py` (`board_data() -> dict`, `render_board(d) -> str`), `agent_job_queue/cli.py`, `agent_job_queue/hooks/session_start.sh`
- Test: `tests/test_projects.py`

**Interfaces:**
- Consumes: Tasks 1–2; `guard.check_all()` from Task 4 (until Task 4 lands, `board_data` calls it via `try/except ImportError` → `[]`).
- Produces: `board_data()` = `{"focus": get_focus(), "last_switch": <last focus.log entry|None>, "projects": [ {name,state,priority,inbox_count,last_touched} sorted active, queued by priority desc, parked, done ], "warnings": [str], "backup": {"last_success": float|None, "ssd_missing": bool}}` (backup read from `HOME/"backup.json"`, Task 6).
- `render_board` first line: `FOCUS <name>` or `FOCUS <name> (waiting on <what> for <age>)` or `FOCUS none (aq focus <project>)`.

- [ ] **Step 1: Failing tests:** `test_board_json` (keys above; focused project first); `test_board_text_waiting` (contains `waiting on render`); `test_session_start_includes_board` (run `sh hooks/session_start.sh` with `PATH` pointing a stub `aq` shim at `python -m agent_job_queue`; `additionalContext` contains `FOCUS` and the sentence `Work only on the focus project; put other requests in with aq idea`).
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** Implement `aq board [--json]`. In `session_start.sh`, append `aq board` output after status and add the rule sentence: "Work only on the focus project; put other requests in with aq idea -p <project>. Switch projects only while the focus is waiting (aq wait) or when the owner says so (aq focus <p> --by owner)."
- [ ] **Step 4:** PASS.
- [ ] **Step 5:** commit `"board: aq board and focus in session-start context"`.

### Task 4: `aq guard`

**Files:**
- Create: `agent_job_queue/guard.py`
- Modify: `agent_job_queue/cli.py`
- Test: `tests/test_guard.py`

**Interfaces:**
- Consumes: `projects.all_projects()`, `conf()`.
- Produces: `check_project(p) -> list[str]`; `check_disks() -> list[str]`; `check_all() -> list[str]` (each warning prefixed `[<project>]` or `[disk]`); `fix_project(p) -> list[str]` (actions taken).
- Config: `ssd_path` (e.g. `/Volumes/<name>`), `media_ext` default `[".mp4",".mov",".wav",".mp3",".m4a",".png",".jpg",".webm",".mkv"]`, `media_mb` default 20, `mac_min_gb` 15, `ssd_min_gb` 50.
- Rules: a project path that does not exist → `path missing` (SSD unplugged), not an error. A path under `ssd_path` skips the media check. Media scan skips `.git`, `node_modules`, `.venv`, `dist`. Unpushed = `git rev-list --count @{u}..` > 0; no upstream → `branch has no upstream`. `gh` missing during `--fix` → action text `gh not installed; repo created locally only`.

- [ ] **Step 1: Failing tests** (real `git` in tmp dirs; set `user.email`/`user.name` per repo):
  - `test_no_repo_warns`: plain dir → warning contains `no git repo`.
  - `test_dirty_and_unpushed`: repo with a bare tmp remote, one commit pushed, one unpushed, one untracked file → warnings contain `uncommitted` and `1 unpushed`.
  - `test_media_on_mac`: 21 MB `.mp4` in a project outside `ssd_path` → `move to SSD`; same file in a project under `ssd_path` (set to a tmp dir) → no media warning.
  - `test_missing_path`: registered path deleted → `path missing`, exit 0.
  - `test_fix_without_gh`: `PATH` without `gh`; `aq guard -p x --fix` → `.git` and `.gitignore` exist, output contains `gh not installed`.
  - `test_guard_never_commits`: after `aq guard` on dirty repo, `git status --porcelain` unchanged.
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** Implement `aq guard [-p P] [--fix] [--json]`; exit 0 always (warnings are output, not failures). Hook it into `board_data()["warnings"]` (disk checks + per-project, but skip the media walk in `board` for speed: `check_project(p, media=False)`).
- [ ] **Step 4:** PASS.
- [ ] **Step 5:** commit `"guard: git, media and disk checks; --fix creates private repo"`.

### Task 5: History move and restore

**Files:**
- Create: `agent_job_queue/backup.py`
- Modify: `agent_job_queue/cli.py`
- Test: `tests/test_backup.py`

**Interfaces:**
- Consumes: `conf()`.
- Produces: `move_history(src: Path, dst: Path, days=7, now=None) -> dict` (`{"moved": n_sessions, "files": n, "skipped": n, "bytes": n}`); `restore(session_id, src, dst) -> Path`.
- Config: `history.src` default `~/.claude/projects`, `history.dst` default `<ssd_path>/claude-history/projects`.
- Unit of a move = one session: `<proj>/<id>.jsonl` plus optional `<proj>/<id>/`. Age = mtime of the `.jsonl` only. Any path containing a `memory` component is never touched. Per file: `shutil.copy2` → compare `st_size` → `unlink`. Any mismatch or copy error → keep the Mac file, count `skipped`, continue. Then remove empty dirs left behind (not `memory`). Destination root missing (SSD not mounted) → raise `SSDMissing`.

- [ ] **Step 1: Failing tests:**
  - `test_moves_old_session_with_folder`: old `p/s1.jsonl` + `p/s1/sub/a.json` (both mtime 10 days ago) → both exist under dst, gone from src.
  - `test_keeps_recent_jsonl_even_if_folder_old`: `s2.jsonl` mtime 1 day, `s2/` 10 days → nothing moved.
  - `test_never_touches_memory`: old `p/memory/MEMORY.md` stays.
  - `test_size_mismatch_keeps_source`: monkeypatch `shutil.copy2` to write a truncated file → src kept, `skipped == 1`.
  - `test_ssd_missing`: dst parent does not exist → `SSDMissing`, src untouched.
  - `test_restore`: after move, `restore("s1")` copies it back to src (dst copy kept).
  - `test_paths_with_spaces`: dst root `<tmp>/SSK SSD/claude-history/projects` works.
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** Implement plus CLI `aq history move [--days N] [--dry-run]`, `aq history restore <session-id>`.
- [ ] **Step 4:** PASS.
- [ ] **Step 5:** commit `"backup: move old Claude sessions to the SSD, restore one back"`.

### Task 6: Drive copy, `aq backup`, LaunchAgent

**Files:**
- Modify: `agent_job_queue/backup.py`, `agent_job_queue/cli.py`
- Test: `tests/test_backup.py`

**Interfaces:**
- Consumes: Task 5 functions; `guard.check_disks()`.
- Produces: `drive_copy(paths: list[str], remote: str, excludes: list[str], runner=subprocess.run) -> list[dict]`; `run_backup() -> dict` (writes `HOME/"backup.json"`: `{"last_run", "last_success", "ssd_missing", "history": {...}, "drive": [...]}` and appends a JSON line to `HOME/"backup.log"`); `plist_text(python: str, aq_home: str) -> str`; `install_backup(uninstall=False)`.
- Config: `ssd_path`; `backup.remote` (e.g. `gdrive:aq-backup`, required for the Drive step; absent → step skipped with `no backup.remote set`); `backup.paths` default `[ssd_path]`; `backup.exclude` default `["tmp/**","work/**","**/cache/**","**/.cache/**","._*",".Spotlight-V100/**",".Trashes/**",".fseventsd/**"]`.
- rclone argv per path: `["rclone","copy",path,f"{remote}/{basename-or-relative}","--max-duration","50m","--transfers","4","--exclude",…]` (one `--exclude` per pattern; `claude-history` sorted first). rclone missing → `{"path":…, "error":"rclone not installed"}`.
- SSD missing (`ssd_path` not a mounted dir) → `ssd_missing: true`, notify via `osascript -e 'display notification "SSD MISSING ..." with title "aq backup"'` (skip if no osascript), no history move, no Drive step.
- LaunchAgent: label `com.agent-job-queue.backup`, `~/Library/LaunchAgents/com.agent-job-queue.backup.plist`, `ProgramArguments = [python, "-m", "agent_job_queue", "backup"]`, `StartInterval 3600`, `RunAtLoad true`, `EnvironmentVariables {AQ_HOME}`, logs to `HOME/"backup.out"`. Install = write plist + `launchctl bootstrap gui/<uid>`; prints the Full Disk Access note naming the python binary.
- A second `aq backup` while one runs → exits 0 with `backup already running` (non-blocking `flock` on `HOME/"backup.lock"`).

- [ ] **Step 1: Failing tests** (inject a fake runner; never call real rclone):
  - `test_drive_copy_argv`: argv has `copy`, never `sync`/`--delete`, has `--max-duration 50m`, one `--exclude` per pattern, path with spaces as one argv element.
  - `test_backup_ssd_missing`: `ssd_path` nonexistent → `backup.json` `ssd_missing true`, runner never called.
  - `test_no_remote_skips_drive`: no `backup.remote` → drive result says `no backup.remote set`, history still moved.
  - `test_plist`: `plistlib.loads(plist_text(...))` has `StartInterval == 3600` and the label.
  - `test_board_shows_backup`: after a successful fake run, `aq board --json` `backup.last_success` is set.
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** Implement `aq backup [--dry-run]`, `aq install backup [--uninstall]`.
- [ ] **Step 4:** PASS.
- [ ] **Step 5:** commit `"backup: hourly LaunchAgent, rclone copy to Drive, status on the board"`.

### Task 7: Focus-blocking hook (opt-in)

**Files:**
- Create: `agent_job_queue/hooks/pre_edit.py`
- Modify: `agent_job_queue/cli.py` (`_claude_hooks`: `--focus` flag adds `PreToolUse` matcher `Edit|Write|NotebookEdit|Bash` running `pre_edit.py`; `HOOK_FILES` gains it), `README.md`
- Test: `tests/test_hook_focus.py`

**Interfaces:**
- Consumes: `projects.match_path`, `projects.get_focus` (hook imports the package from its own parent dir: `sys.path.insert(0, str(Path(__file__).resolve().parents[1]))`).
- Input: hook JSON `{tool_name, tool_input: {file_path|notebook_path|command}, cwd}`. Bash: target = `cwd`; if command matches `\b(aq|agent-job-queue)\b` → allow.
- Deny when target project ≠ focus project, focus set, and `waiting_on` is None. Message: `focus is <F>; add this with: aq idea -p <P> "<what>"  (or the owner switches: aq focus <P> --by owner)`.

- [ ] **Step 1: Failing tests:** deny Edit in non-focus project; allow Edit in focus project; allow when focus waiting; allow unregistered path; allow Bash `aq idea ...` in other project; allow garbage stdin; allow when no focus set; `aq install claude --focus` twice → one entry; `--uninstall` removes it.
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** PASS.
- [ ] **Step 5:** commit `"hook: opt-in focus guard for Edit/Write/Bash"`.

### Task 8: Docs, rules block, version

**Files:** `README.md`, `CHANGELOG.md`, `agent_job_queue/__init__.py` (`0.3.0`), `cli.py` (`RULE` text + module docstring)

- [ ] **Step 1:** README sections: Projects and focus, Board, Guard, Backup (with rclone remote setup and own-client-ID note), Focus hook. CHANGELOG 0.3.0. RULE block gains: "Work on the focus project (`aq board`); new ideas for others go in `aq idea -p <project>`; before finishing a task run `aq guard -p <project>` and push."
- [ ] **Step 2:** `python3 -m pytest -q` → PASS; `python3 -m agent_job_queue --help` lists new commands.
- [ ] **Step 3:** commit `"0.3.0: control plane docs"`; `git push -u origin control-plane`.

## Not in this plan (ops, after the build)

- Register the owner's projects (`aq project scan ~/code "/Volumes/SSK SSD"` → review → add), set `ssd_path`, `backup.remote`, run `aq install backup` and `aq install claude --focus` only with the owner's OK.
- The one-time sweep of the old chats (spec §5).
