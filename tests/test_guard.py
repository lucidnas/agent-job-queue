import importlib, json, os, subprocess, sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
AQ = [sys.executable, "-m", "agent_job_queue"]


def git(d, *a): return subprocess.run(["git", "-C", str(d), *a], capture_output=True, text=True, check=True).stdout


def repo(d):
    d.mkdir(parents=True, exist_ok=True); git(d, "init", "-q", "-b", "main")
    git(d, "config", "user.email", "t@example.com"); git(d, "config", "user.name", "t"); return d


@pytest.fixture
def G(tmp_path, monkeypatch):
    monkeypatch.setenv("AQ_HOME", str(tmp_path / "state")); monkeypatch.setenv("HOME", str(tmp_path / "home"))
    import agent_job_queue.state, agent_job_queue.projects, agent_job_queue.guard
    importlib.reload(agent_job_queue.state); importlib.reload(agent_job_queue.projects)
    return importlib.reload(agent_job_queue.guard)


def P(): import agent_job_queue.projects as p; return p


def test_no_repo_warns(tmp_path, G):
    d = tmp_path / "plain"; d.mkdir(); P().add("plain", [str(d)])
    assert any("no git repo" in w for w in G.check_project(P().load("plain")))


def test_dirty_and_unpushed(tmp_path, G):
    bare = tmp_path / "remote.git"; subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    d = repo(tmp_path / "r"); git(d, "remote", "add", "origin", str(bare))
    (d / "a").write_text("1"); git(d, "add", "a"); git(d, "commit", "-qm", "1"); git(d, "push", "-qu", "origin", "main")
    (d / "b").write_text("2"); git(d, "add", "b"); git(d, "commit", "-qm", "2"); (d / "c").write_text("3")
    P().add("r", [str(d)])
    w = " ".join(G.check_project(P().load("r")))
    assert "uncommitted" in w and "1 unpushed" in w


def test_no_upstream(tmp_path, G):
    d = repo(tmp_path / "r"); (d / "a").write_text("1"); git(d, "add", "a"); git(d, "commit", "-qm", "1")
    P().add("r", [str(d)])
    assert any("no upstream" in w for w in G.check_project(P().load("r")))


def test_media_on_mac(tmp_path, G):
    mac = repo(tmp_path / "mac"); (mac / "big.mp4").write_bytes(b"\0" * (21 * 1024 * 1024))
    ssd = tmp_path / "ssd"; s = repo(ssd / "proj"); (s / "big.mp4").write_bytes(b"\0" * (21 * 1024 * 1024))
    import agent_job_queue.state as st; st.write_conf({"ssd_path": str(ssd)})
    P().add("mac", [str(mac)]); P().add("onssd", [str(s)])
    assert any("move to SSD" in w for w in G.check_project(P().load("mac")))
    assert not any("move to SSD" in w for w in G.check_project(P().load("onssd")))


def test_missing_path(tmp_path, G):
    P().add("gone", [str(tmp_path / "nope")])
    assert any("path missing" in w for w in G.check_project(P().load("gone")))


def test_fix_without_gh(tmp_path):
    e = dict(os.environ, AQ_HOME=str(tmp_path / "state"), HOME=str(tmp_path / "home"), PYTHONPATH=str(ROOT),
             PATH="/usr/bin:/bin", GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com")
    (tmp_path / "home").mkdir(); d = tmp_path / "x"; d.mkdir()
    subprocess.run(AQ + ["project", "add", "x", "--path", str(d)], env=e, check=True, capture_output=True)
    r = subprocess.run(AQ + ["guard", "-p", "x", "--fix"], env=e, capture_output=True, text=True)
    assert r.returncode == 0 and (d / ".git").exists() and (d / ".gitignore").exists() and "gh not installed" in r.stdout


def test_guard_never_commits(tmp_path, G):
    d = repo(tmp_path / "r"); (d / "a").write_text("1"); P().add("r", [str(d)])
    before = git(d, "status", "--porcelain"); G.check_all(); assert git(d, "status", "--porcelain") == before


def test_disk_warning(tmp_path, G):
    import agent_job_queue.state as st; st.write_conf({"mac_min_gb": 10 ** 6})
    assert any(w.startswith("[disk]") and "Mac" in w for w in G.check_disks())
