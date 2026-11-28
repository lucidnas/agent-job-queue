import json, os, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AQ = [sys.executable, "-m", "agent_job_queue"]
HOOK = ROOT / "agent_job_queue/hooks/pre_edit.py"


def env(tmp):
    (tmp / "home").mkdir(exist_ok=True)
    return dict(os.environ, AQ_HOME=str(tmp / "state"), HOME=str(tmp / "home"), PYTHONPATH=str(ROOT))


def aq(tmp, *a): subprocess.run(AQ + list(a), env=env(tmp), check=True, capture_output=True)


def two_projects(tmp):
    for n in ("a", "b"): (tmp / n).mkdir(exist_ok=True); aq(tmp, "project", "add", n, "--path", str(tmp / n))
    aq(tmp, "focus", "a")


def denied(tmp, payload):
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    p = subprocess.run([sys.executable, str(HOOK)], input=raw, env=env(tmp), capture_output=True, text=True)
    assert p.returncode == 0
    return "deny" in p.stdout


def edit(path): return {"tool_name": "Edit", "tool_input": {"file_path": str(path)}, "cwd": "/"}


def test_deny_other_project(tmp_path):
    two_projects(tmp_path); assert denied(tmp_path, edit(tmp_path / "b/x.py"))


def test_allow_focus_project(tmp_path):
    two_projects(tmp_path); assert not denied(tmp_path, edit(tmp_path / "a/x.py"))


def test_allow_while_waiting(tmp_path):
    two_projects(tmp_path); aq(tmp_path, "wait", "render"); assert not denied(tmp_path, edit(tmp_path / "b/x.py"))


def test_allow_unregistered(tmp_path):
    two_projects(tmp_path); assert not denied(tmp_path, edit(tmp_path / "elsewhere/x.py"))


def test_bash_cwd(tmp_path):
    two_projects(tmp_path)
    assert denied(tmp_path, {"tool_name": "Bash", "tool_input": {"command": "make"}, "cwd": str(tmp_path / "b")})
    assert not denied(tmp_path, {"tool_name": "Bash", "tool_input": {"command": 'aq idea -p b "x"'}, "cwd": str(tmp_path / "b")})


def test_notebook(tmp_path):
    two_projects(tmp_path); assert denied(tmp_path, {"tool_name": "NotebookEdit", "tool_input": {"notebook_path": str(tmp_path / "b/n.ipynb")}})


def test_garbage_and_no_focus(tmp_path):
    assert not denied(tmp_path, "not json")
    for n in ("a", "b"): (tmp_path / n).mkdir(); aq(tmp_path, "project", "add", n, "--path", str(tmp_path / n))
    assert not denied(tmp_path, edit(tmp_path / "b/x.py"))


def test_install_focus_idempotent(tmp_path):
    e = env(tmp_path)
    for _ in range(2): subprocess.run(AQ + ["install", "claude", "--focus"], env=e, check=True, capture_output=True)
    s = json.loads((tmp_path / "home/.claude/settings.json").read_text())
    pre = [h for x in s["hooks"]["PreToolUse"] for h in x["hooks"] if "pre_edit.py" in h["command"]]
    assert len(pre) == 1 and len(s["hooks"]["SessionStart"]) == 1
    subprocess.run(AQ + ["install", "--uninstall", "claude"], env=e, check=True, capture_output=True)
    assert "hooks" not in json.loads((tmp_path / "home/.claude/settings.json").read_text())


def test_bash_reads_allowed_in_other_project(tmp_path):
    two_projects(tmp_path); b = str(tmp_path / "b")
    for cmd in ("ls -la", "git log --oneline | head -5", "cat x.py && git status"):
        assert not denied(tmp_path, {"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": b}), cmd
    assert denied(tmp_path, {"tool_name": "Bash", "tool_input": {"command": "cat a > b.txt"}, "cwd": b})
    assert denied(tmp_path, {"tool_name": "Bash", "tool_input": {"command": "ls && rm -rf x"}, "cwd": b})


def test_more_reads_and_writes(tmp_path):
    two_projects(tmp_path); b = str(tmp_path / "b")
    bash = lambda c: denied(tmp_path, {"tool_name": "Bash", "tool_input": {"command": c}, "cwd": b})
    for c in ("ls 2>/dev/null", "cd x && git status", "sed -n 1,5p f", "git -C x log", "jq . f", 'grep "a>b" f', "aq board 2>&1 | head"):
        assert not bash(c), c
    for c in ('git commit -m "fix aq"', "sed -i '' s/a/b/ f", "find . -delete", "npm run build", "echo x > f"):
        assert bash(c), c


def test_install_keeps_user_hooks_and_block(tmp_path):
    e = env(tmp_path); f = tmp_path / "home/.claude/settings.json"; f.parent.mkdir(parents=True)
    mine = {"type": "command", "command": "python3 ~/myhooks/hooks/pre_bash.py"}
    f.write_text(json.dumps({"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [mine]}]}}))
    subprocess.run(AQ + ["install", "claude", "--block"], env=e, check=True, capture_output=True)
    subprocess.run(AQ + ["install", "claude", "--focus"], env=e, check=True, capture_output=True)
    cmds = [h["command"] for x in json.loads(f.read_text())["hooks"]["PreToolUse"] for h in x["hooks"]]
    assert mine["command"] in cmds and any("pre_bash.py" in c and "agent_job_queue" in c for c in cmds) and any("pre_edit.py" in c for c in cmds)
    subprocess.run(AQ + ["install", "claude", "--no-block"], env=e, check=True, capture_output=True)
    cmds = [h["command"] for x in json.loads(f.read_text())["hooks"]["PreToolUse"] for h in x["hooks"]]
    assert mine["command"] in cmds and not any("agent_job_queue/hooks/pre_bash.py" in c for c in cmds)
    subprocess.run(AQ + ["install", "--uninstall", "claude"], env=e, check=True, capture_output=True)
    assert [h["command"] for x in json.loads(f.read_text())["hooks"]["PreToolUse"] for h in x["hooks"]] == [mine["command"]]


def decision(tmp, payload):
    p = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload), env=env(tmp), capture_output=True, text=True)
    return json.loads(p.stdout)["hookSpecificOutput"]["permissionDecision"] if p.stdout.strip() else "allow"


def test_paths_inside_commands(tmp_path):
    two_projects(tmp_path); a, b = str(tmp_path / "a"), str(tmp_path / "b")
    run = lambda c: decision(tmp_path, {"tool_name": "Bash", "tool_input": {"command": c}, "cwd": a})
    for c in (f"cp x {b}/y", f"echo hi > {b}/f", f"cd {b} && npm run build", "cd ../b && rm f", f"git -C {b} commit -m x", f"mv {b}/x ./x"):
        assert run(c) == "deny", c
    for c in ("make", f"cp {b}/y ./y", f'git commit -m "see {b}"', f"cat {b}/f", f"ls {b} > /dev/null", f"rsync -a {b}/ ./copy/"):
        assert run(c) == "allow", c


def test_only_owner_switches_focus(tmp_path):
    two_projects(tmp_path); a = str(tmp_path / "a")
    run = lambda c: decision(tmp_path, {"tool_name": "Bash", "tool_input": {"command": c}, "cwd": a})
    for c in ("aq focus b", "aq focus b --by owner", "python3 -m agent_job_queue focus b", f"rm {tmp_path}/state/focus.json"):
        assert run(c) == "deny", c
    for c in ("aq focus a", "aq focus --json", "aq focus", 'aq wait "render"', "aq resume"):
        assert run(c) == "allow", c
    assert decision(tmp_path, {"tool_name": "Write", "tool_input": {"file_path": str(tmp_path / "state/focus.json")}}) == "deny"


def test_idea_to_inbox_by_name(tmp_path):
    aq(tmp_path, "idea", "-p", "inbox", "x")
