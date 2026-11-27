import importlib, json, os, subprocess, sys, time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
AQ = [sys.executable, "-m", "agent_job_queue"]


def env(tmp, **kw):
    (tmp / "home").mkdir(exist_ok=True)
    e = dict(os.environ, AQ_HOME=str(tmp / "state"), HOME=str(tmp / "home"), PYTHONPATH=str(ROOT))
    e.pop("CLAUDE_SESSION_ID", None); e.update(kw); return e


def aq(tmp, *args, cwd=None, check=True, **kw):
    return subprocess.run(AQ + list(args), env=env(tmp, **kw), cwd=cwd, capture_output=True, text=True, check=check)


@pytest.fixture
def P(tmp_path, monkeypatch):
    monkeypatch.setenv("AQ_HOME", str(tmp_path / "state")); monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    import agent_job_queue.state, agent_job_queue.projects
    importlib.reload(agent_job_queue.state); return importlib.reload(agent_job_queue.projects)


def test_add_and_ls(tmp_path):
    d = tmp_path / "SSK SSD" / "vid"; d.mkdir(parents=True)
    aq(tmp_path, "project", "add", "vid", "--path", str(d), "--goal", "ship")
    ls = json.loads(aq(tmp_path, "project", "ls", "--json").stdout)
    assert ls[0]["name"] == "vid" and ls[0]["state"] == "queued" and ls[0]["paths"] == [str(d)] and ls[0]["goal"] == "ship"


def test_match_longest_and_boundary(tmp_path, P):
    (tmp_path / "x/y").mkdir(parents=True); (tmp_path / "xy").mkdir()
    P.add("a", [str(tmp_path / "x")]); P.add("b", [str(tmp_path / "x/y")])
    assert P.match_path(str(tmp_path / "x/y/z")) == "b"
    assert P.match_path(str(tmp_path / "x/q")) == "a"
    assert P.match_path(str(tmp_path / "xy")) is None
    assert P.match_path(str(tmp_path / "x") + "/") == "a"


def test_match_symlink(tmp_path, P):
    (tmp_path / "x").mkdir(); (tmp_path / "link").symlink_to(tmp_path / "x")
    P.add("a", [str(tmp_path / "x")])
    assert P.match_path(str(tmp_path / "link/f")) == "a"


def test_run_uses_registered_project(tmp_path):
    d = tmp_path / "x/y"; d.mkdir(parents=True)
    aq(tmp_path, "project", "add", "b", "--path", str(d))
    p = subprocess.Popen(AQ + ["run", "--", sys.executable, "-c", "import time; time.sleep(3)"], env=env(tmp_path), cwd=d, stderr=subprocess.DEVNULL)
    try:
        for _ in range(20):
            js = json.loads(aq(tmp_path, "status", "--json").stdout)["jobs"]
            if js: break
            time.sleep(0.2)
        assert js[0]["project"] == "b"
    finally:
        p.wait(timeout=30)


def test_focus_switch_logs(tmp_path, P):
    P.add("a", [str(tmp_path)]); P.add("b", [str(tmp_path / "b")])
    aq(tmp_path, "focus", "a"); aq(tmp_path, "focus", "b", "--by", "s1")
    assert P.load("a")["state"] == "queued" and P.load("b")["state"] == "active"
    last = json.loads((tmp_path / "state/focus.log").read_text().splitlines()[-1])
    assert (last["from"], last["to"], last["by"]) == ("a", "b", "s1")


def test_wait_resume(tmp_path, P):
    P.add("a", [str(tmp_path)]); aq(tmp_path, "focus", "a")
    aq(tmp_path, "wait", "render"); assert P.get_focus()["waiting_on"] == "render"
    aq(tmp_path, "resume"); assert P.get_focus()["waiting_on"] is None


def test_idea_default_inbox(tmp_path, P):
    aq(tmp_path, "idea", "try stickman")
    inbox = P.load("inbox")["inbox"]
    assert len(inbox) == 1 and inbox[0]["text"] == "try stickman" and inbox[0]["by"] == "owner" and inbox[0]["t"] > 0


def test_idea_by_session(tmp_path, P):
    P.add("a", [str(tmp_path)])
    aq(tmp_path, "idea", "x", "-p", "a", CLAUDE_SESSION_ID="sess9")
    assert P.load("a")["inbox"][0]["by"] == "sess9"


def test_next(tmp_path, P):
    for n, pr in (("a", 0), ("b", 5), ("c", 1), ("d", 9)): P.add(n, [str(tmp_path / n)], priority=pr)
    P.set_focus("a", "owner")
    assert P.next_up()["project"] == "a"
    P.add_idea("i1", "b"); P.add_idea("i2", "c"); P.wait("render")   # d has no ideas: skipped
    assert P.next_up()["project"] == "b"
    P.resume(); assert P.next_up()["project"] == "a"


def test_focus_unknown(tmp_path):
    r = aq(tmp_path, "focus", "nope", check=False)
    assert r.returncode == 2 and "no project" in r.stderr


def test_board_json(tmp_path, P):
    P.add("a", [str(tmp_path / "a")]); P.add("b", [str(tmp_path / "b")], priority=3); P.set_focus("a", "owner")
    d = json.loads(aq(tmp_path, "board", "--json").stdout)
    assert set(d) >= {"focus", "last_switch", "projects", "warnings", "backup"}
    assert d["projects"][0]["name"] == "a" and d["focus"]["project"] == "a"


def test_board_text_waiting(tmp_path, P):
    P.add("a", [str(tmp_path / "a")]); P.set_focus("a", "owner"); P.wait("render")
    out = aq(tmp_path, "board").stdout
    assert out.startswith("FOCUS a") and "waiting on render" in out


def test_board_no_focus(tmp_path):
    assert aq(tmp_path, "board").stdout.startswith("FOCUS none")


def test_session_start_includes_board(tmp_path):
    b = tmp_path / "bin"; b.mkdir()
    (b / "aq").write_text(f"#!/bin/sh\nexec {sys.executable} -m agent_job_queue \"$@\"\n"); (b / "aq").chmod(0o755)
    e = env(tmp_path, PATH=f"{b}:{os.environ['PATH']}")
    out = subprocess.run(["sh", str(ROOT / "agent_job_queue/hooks/session_start.sh")], env=e, capture_output=True, text=True, check=True).stdout
    ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
    assert "FOCUS" in ctx and "Work only on the focus project; put other requests in with aq idea" in ctx
