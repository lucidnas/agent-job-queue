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
