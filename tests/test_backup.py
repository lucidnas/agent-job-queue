import importlib, json, os, plistlib, shutil, subprocess, sys, time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
AQ = [sys.executable, "-m", "agent_job_queue"]
OLD = time.time() - 10 * 86400; NEW = time.time() - 86400


@pytest.fixture
def B(tmp_path, monkeypatch):
    monkeypatch.setenv("AQ_HOME", str(tmp_path / "state")); monkeypatch.setenv("HOME", str(tmp_path / "home"))
    import agent_job_queue.state, agent_job_queue.projects, agent_job_queue.guard, agent_job_queue.backup
    for m in (agent_job_queue.state, agent_job_queue.projects, agent_job_queue.guard): importlib.reload(m)
    return importlib.reload(agent_job_queue.backup)


def touch(p, t, data="x"):
    p.parent.mkdir(parents=True, exist_ok=True); p.write_text(data); os.utime(p, (t, t)); return p


def tree(tmp):
    src = tmp / "home/.claude/projects"; ssd = tmp / "SSK SSD"; (ssd).mkdir(parents=True)
    return src, ssd / "claude-history/projects"


def test_moves_old_session_with_folder(tmp_path, B):
    src, dst = tree(tmp_path)
    touch(src / "p/s1.jsonl", OLD); touch(src / "p/s1/sub/a.json", OLD)
    r = B.move_history(src, dst)
    assert r["moved"] == 1 and (dst / "p/s1.jsonl").exists() and (dst / "p/s1/sub/a.json").exists()
    assert not (src / "p/s1.jsonl").exists() and not (src / "p/s1").exists()


def test_keeps_recent_jsonl_even_if_folder_old(tmp_path, B):
    src, dst = tree(tmp_path)
    touch(src / "p/s2.jsonl", NEW); touch(src / "p/s2/a.json", OLD)
    assert B.move_history(src, dst)["moved"] == 0 and (src / "p/s2/a.json").exists()


def test_never_touches_memory(tmp_path, B):
    src, dst = tree(tmp_path)
    touch(src / "p/memory/MEMORY.md", OLD); touch(src / "p/memory/old.jsonl", OLD)
    B.move_history(src, dst)
    assert (src / "p/memory/MEMORY.md").exists() and (src / "p/memory/old.jsonl").exists()


def test_size_mismatch_keeps_source(tmp_path, B, monkeypatch):
    src, dst = tree(tmp_path); touch(src / "p/s1.jsonl", OLD, "hello world")
    monkeypatch.setattr(B.shutil, "copy2", lambda a, b: Path(b).write_text("he"))
    r = B.move_history(src, dst)
    assert (src / "p/s1.jsonl").exists() and r["skipped"] == 1 and r["moved"] == 0


def test_ssd_missing(tmp_path, B):
    src = tmp_path / "src"; touch(src / "p/s1.jsonl", OLD)
    with pytest.raises(B.SSDMissing):
        B.move_history(src, tmp_path / "nossd/claude-history/projects", root=tmp_path / "nossd")
    assert (src / "p/s1.jsonl").exists()


def test_restore(tmp_path, B):
    src, dst = tree(tmp_path); touch(src / "p/s1.jsonl", OLD); touch(src / "p/s1/a.json", OLD)
    B.move_history(src, dst)
    B.restore("s1", src, dst)
    assert (src / "p/s1.jsonl").exists() and (src / "p/s1/a.json").exists() and (dst / "p/s1.jsonl").exists()


def test_already_copied_is_not_rewritten(tmp_path, B, monkeypatch):
    src, dst = tree(tmp_path); touch(src / "p/s1.jsonl", OLD, "abc"); touch(dst / "p/s1.jsonl", OLD, "abc")
    monkeypatch.setattr(B.shutil, "copy2", lambda a, b: (_ for _ in ()).throw(AssertionError("copied again")))
    assert B.move_history(src, dst)["moved"] == 1 and not (src / "p/s1.jsonl").exists()


class Runner:
    def __init__(self): self.calls = []
    def __call__(self, argv, **kw): self.calls.append(argv); return subprocess.CompletedProcess(argv, 0, "", "")


def test_drive_copy_argv(tmp_path, B, monkeypatch):
    monkeypatch.setattr(B.shutil, "which", lambda n: "/x/" + n)
    ssd = tmp_path / "SSK SSD"; (ssd / "claude-history").mkdir(parents=True)
    run = Runner(); B.drive_copy([str(ssd)], "gd:aq-backup", ["tmp/**", "._*"], runner=run)
    first, flat = run.calls[0], [a for c in run.calls for a in c]
    assert first[1] == "copy" and first[2] == str(ssd / "claude-history") and "--backup-dir" in first      # history goes first, path with space is one arg
    assert "sync" not in flat and not any(a.startswith("--delete") for a in flat)
    assert first[first.index("--max-duration") + 1].endswith("m") and first.count("--exclude") == 2


def test_drive_copy_no_rclone(tmp_path, B, monkeypatch):
    monkeypatch.setattr(B.shutil, "which", lambda n: None)
    r = B.drive_copy([str(tmp_path)], "gd:x", [], runner=Runner())
    assert r[0]["error"] == "rclone not installed"


def test_backup_ssd_missing(tmp_path, B):
    cfg(ssd_path=str(tmp_path / "nossd"), backup={"remote": "gd:x"})
    run = Runner(); r = B.run_backup(runner=run, notify=False)
    assert r["ssd_missing"] and run.calls == []
    assert json.loads((tmp_path / "state/backup.json").read_text())["ssd_missing"] is True


def cfg(**kw):
    import agent_job_queue.state as st; st.write_conf(dict({"ssd_must_be_mount": False}, **kw))


def test_no_remote_keeps_mac_copy(tmp_path, B):
    ssd = tmp_path / "SSK SSD"; ssd.mkdir()
    src = tmp_path / "home/.claude/projects"; touch(src / "p/s1.jsonl", OLD)
    cfg(ssd_path=str(ssd))
    r = B.run_backup(runner=Runner(), notify=False)
    assert r["drive"] == [{"skipped": "no backup.remote set"}] and r["last_success"]
    assert (src / "p/s1.jsonl").exists() and (ssd / "claude-history/projects/p/s1.jsonl").exists()   # two copies, never one


class CheckRunner(Runner):
    def __init__(self, check_rc): super().__init__(); self.check_rc = check_rc
    def __call__(self, argv, **kw):
        self.calls.append(argv); return subprocess.CompletedProcess(argv, self.check_rc if argv[1] == "check" else 0, "", "")


@pytest.mark.parametrize("check_rc,removed", [(0, True), (1, False)])
def test_mac_copy_removed_only_when_remote_confirms(tmp_path, B, monkeypatch, check_rc, removed):
    monkeypatch.setattr(B.shutil, "which", lambda n: "/x/" + n)
    ssd = tmp_path / "SSK SSD"; ssd.mkdir(); src = tmp_path / "home/.claude/projects"; touch(src / "p/s1.jsonl", OLD)
    cfg(ssd_path=str(ssd), backup={"remote": "gd:aq-backup"})
    r = B.run_backup(runner=CheckRunner(check_rc), notify=False)
    assert (src / "p/s1.jsonl").exists() != removed and (ssd / "claude-history/projects/p/s1.jsonl").exists()
    assert r["history"]["on_remote"] is removed


def test_leftover_folder_is_not_an_ssd(tmp_path, B):
    ssd = tmp_path / "SSK SSD"; ssd.mkdir()                   # a plain folder, not a mount point
    import agent_job_queue.state as st; st.write_conf({"ssd_path": str(ssd)})
    assert B.run_backup(runner=Runner(), notify=False)["ssd_missing"]


def test_restore_refuses_overwrite_and_marks_fresh(tmp_path, B):
    src, dst = tree(tmp_path); touch(src / "p/s1.jsonl", OLD); B.move_history(src, dst)
    t = B.restore("s1", src, dst); assert t.stat().st_mtime > time.time() - 60
    with pytest.raises(FileExistsError): B.restore("s1", src, dst)
    assert list(B.sessions(src)) == []                        # next hourly run won't move it straight back


def test_versions_dir(B):
    assert B.versions_dir("gd:aq-backup", "2026-11-26") == "gd:aq-backup-versions/2026-11-26"
    assert B.versions_dir("gd:", "d") == "gd:aq-versions/d"


def test_plist(B):
    d = plistlib.loads(B.plist_text("/usr/bin/python3", "/tmp/a q").encode())
    assert d["Label"] == "com.agent-job-queue.backup" and d["StartInterval"] == 3600
    assert d["ProgramArguments"][-1] == "backup" and d["EnvironmentVariables"]["AQ_HOME"] == "/tmp/a q"
    assert d["EnvironmentVariables"]["PYTHONPATH"] == str(ROOT) and d["WorkingDirectory"] == str(ROOT)   # importable outside the checkout


def test_board_shows_backup(tmp_path, B):
    ssd = tmp_path / "ssd"; ssd.mkdir(); cfg(ssd_path=str(ssd))
    B.run_backup(runner=Runner(), notify=False)
    e = dict(os.environ, AQ_HOME=str(tmp_path / "state"), HOME=str(tmp_path / "home"), PYTHONPATH=str(ROOT))
    d = json.loads(subprocess.run(AQ + ["board", "--json"], env=e, capture_output=True, text=True, check=True).stdout)
    assert d["backup"]["last_success"]
