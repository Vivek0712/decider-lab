"""Workspace scan: labs, run roots, ids, path safety, state dir."""

from __future__ import annotations

import os
import stat

import pytest

from decider_lab.ui.errors import ApiError
from decider_lab.ui.workspace import Workspace, decode_id, encode_id


def test_scan_finds_labs_and_ignores_the_rest(workspace):
    ws = Workspace(str(workspace))
    labs = {lab.path: lab for lab in ws.labs()}
    assert set(labs) == {"labs/first/lab.yaml", "labs/second/lab.yaml", "labs/broken/lab.yaml"}
    assert labs["labs/first/lab.yaml"].name == "first-lab" and labs["labs/first/lab.yaml"].parsed
    broken = labs["labs/broken/lab.yaml"]
    assert broken.name == "broken-lab" and not broken.parsed and broken.error
    assert decode_id(labs["labs/first/lab.yaml"].lab_id) == "labs/first/lab.yaml"


def test_scan_respects_depth_and_ignored_dirs(workspace):
    deep = workspace / "a" / "b" / "c" / "d" / "e"
    deep.mkdir(parents=True)
    (deep / "lab.yaml").write_text("models: {m: {baseline: uniform}}\n")
    (workspace / "a" / "b" / "lab.yaml").write_text("models: {m: {baseline: uniform}}\n")
    for ignored in (".git", ".venv", "node_modules"):
        (workspace / ignored).mkdir(exist_ok=True)
        (workspace / ignored / "lab.yaml").write_text("models: {m: {baseline: uniform}}\n")
    paths = {lab.path for lab in Workspace(str(workspace)).labs()}
    assert "a/b/lab.yaml" in paths
    assert "a/b/c/d/e/lab.yaml" not in paths  # depth 5
    assert not any(p.startswith((".git", ".venv", "node_modules")) for p in paths)


def test_run_roots(workspace):
    roots = Workspace(str(workspace)).run_roots()
    assert [r.path for r in roots] == ["labs/first/runs/first-lab"]
    r = roots[0]
    assert r.kind == "lab" and r.title == "first-lab" and r.has_report and r.has_lab_json
    assert set(r.models) == {"fake", "majority", "uniform"}
    assert r.finished_at and r.finished_at.endswith("Z")


def test_eval_output_is_a_run_root(workspace):
    d = workspace / "runs" / "quick" / "smoke"
    d.mkdir(parents=True)
    (d / "scores.json").write_text("{}")
    paths = {r.path: r for r in Workspace(str(workspace)).run_roots()}
    assert paths["runs"].kind == "eval" and paths["runs"].models == ["quick"]


def test_ids_and_paths_stay_inside(workspace):
    ws = Workspace(str(workspace))
    assert ws.decode_id(encode_id("labs/first/lab.yaml")) == os.path.join(ws.root, "labs", "first", "lab.yaml")
    for bad in ("../outside", "/etc/passwd", "labs/../../x"):
        with pytest.raises(ApiError) as e:
            ws.decode_id(encode_id(bad))
        assert e.value.code == "path_outside_workspace"
    with pytest.raises(ApiError):
        decode_id("@@not-base64@@")
    assert ws.rel(os.path.join(ws.root, "labs")) == "labs"


def test_symlink_escape_is_refused(workspace, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace / "link").symlink_to(outside)
    with pytest.raises(ApiError):
        Workspace(str(workspace)).resolve("link/file.txt")


def test_state_dir_is_private_and_gitignored(workspace):
    ws = Workspace(str(workspace))
    ws.ensure_state_dir()
    mode = stat.S_IMODE(os.stat(ws.state_dir).st_mode)
    assert mode == 0o700
    lines = (workspace / ".gitignore").read_text().splitlines()
    assert ".decider-lab-studio/" in lines
    ws.ensure_state_dir()
    assert (workspace / ".gitignore").read_text().count(".decider-lab-studio/") == 1


def test_no_gitignore_is_created(empty_workspace):
    Workspace(str(empty_workspace)).ensure_state_dir()
    assert not (empty_workspace / ".gitignore").exists()
