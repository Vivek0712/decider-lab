from __future__ import annotations

import hashlib
import io
import json
import os
import tarfile
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest
import yaml

from decider_lab import sources
from decider_lab.compute import base


def make_tar(path, files, top="ckpt"):
    with tarfile.open(path, "w") as t:
        for name, data in files.items():
            info = tarfile.TarInfo(f"{top}/{name}" if top else name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


@pytest.fixture
def http_dir(tmp_path):
    d = tmp_path / "www"
    d.mkdir()

    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(Quiet, directory=str(d)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield d, f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


CKPT = {"strands_decider_config.json": b"{}", "lora/adapter.safetensors": b"x" * 100}


def test_kind_of():
    assert sources.kind_of("hf://a/b@c") == "hf"
    assert sources.kind_of("StrandsAgents/strands-decider-2B-hobson-v19") == "hf"
    assert sources.kind_of("s3://b/k.tar") == "s3"
    assert sources.kind_of("https://x/y.tar?sig=1") == "url"
    assert sources.kind_of("./checkpoints/run") == "local"


def test_url_archive_is_verified_extracted_cached_and_query_never_recorded(http_dir, monkeypatch):
    d, url = http_dir
    sha = make_tar(d / "m.tar", CKPT)
    path, info = sources.resolve(f"{url}/m.tar?X-Amz-Signature=secret", {"sha256": sha}, log=lambda *_: None)
    assert os.path.exists(os.path.join(path, "strands_decider_config.json")) and path.endswith("ckpt")
    assert info["sha256"] == sha and "secret" not in json.dumps(info)
    marker = json.load(open(os.path.join(os.path.dirname(path), ".decider-lab-source.json")))
    assert "secret" not in json.dumps(marker)
    # second pull: from the cache, even with a different signature
    monkeypatch.setattr(sources, "_download", lambda *a, **k: pytest.fail("downloaded again"))
    assert sources.resolve(f"{url}/m.tar?X-Amz-Signature=other", {"sha256": sha}, log=lambda *_: None)[0] == path


def test_url_sha_mismatch_fails_and_leaves_nothing(http_dir):
    d, url = http_dir
    make_tar(d / "m.tar", CKPT)
    with pytest.raises(ValueError, match="sha256 does not match"):
        sources.resolve(f"{url}/m.tar", {"sha256": "0" * 64}, log=lambda *_: None)
    assert not [x for x in os.listdir(sources._models_dir()) if not x.startswith(".")]


def test_archive_path_traversal_is_refused(http_dir):
    d, url = http_dir
    make_tar(d / "evil.tar", {"../../escape.txt": b"x"}, top="")
    with pytest.raises(ValueError, match="unsafe path"):
        sources.resolve(f"{url}/evil.tar", log=lambda *_: None)


def test_local_dir_finds_the_checkpoint(tmp_path):
    ck = tmp_path / "run" / "checkpoint"
    ck.mkdir(parents=True)
    (ck / "strands_decider_config.json").write_text("{}")
    path, info = sources.resolve(str(tmp_path / "run"))
    assert path == str(ck) and info["kind"] == "local"
    with pytest.raises(FileNotFoundError):
        sources.resolve(str(tmp_path / "nope"))


def test_hf_policy_pinned_and_recorded(monkeypatch, tmp_path):
    import huggingface_hub

    commit = "b" * 40
    snap = tmp_path / "snapshots" / commit
    snap.mkdir(parents=True)
    (snap / "strands_decider_config.json").write_text("{}")
    calls = []
    monkeypatch.setattr(huggingface_hub, "snapshot_download",
                        lambda repo, **kw: calls.append((repo, kw)) or str(snap))
    path, info = sources.resolve(f"hf://org/model@{commit}", log=lambda *_: None)
    assert path == str(snap) and info["resolved_commit"] == commit and info["pinned"]
    assert calls[0] == ("org/model", {"revision": commit, "token": None, "allow_patterns": None})
    logs = []
    sources.resolve("org/model", log=logs.append)
    assert any("not pinned" in m for m in logs)
    with pytest.raises(ValueError, match="require_pinned"):
        sources.resolve("hf://org/model@main", {"require_pinned": True}, log=lambda *_: None)


class FakeS3:
    def __init__(self, objects):
        self.objects = objects  # key -> bytes

    def download_file(self, bucket, key, dst):
        with open(dst, "wb") as fh:
            fh.write(self.objects[key])

    def get_paginator(self, name):
        objs = self.objects

        class P:
            def paginate(self, Bucket, Prefix):
                yield {"Contents": [{"Key": k, "Size": len(v), "ETag": hashlib.md5(v).hexdigest()}
                                    for k, v in objs.items() if k.startswith(Prefix)]}
        return P()

    def generate_presigned_url(self, op, Params, ExpiresIn):
        return f"https://{Params['Bucket']}.s3.amazonaws.com/{Params['Key']}?X-Amz-Signature=sig&exp={ExpiresIn}"


def test_s3_prefix_and_archive(monkeypatch, tmp_path):
    tar = tmp_path / "m.tar"
    make_tar(tar, CKPT)
    fake = FakeS3({"w/run7/strands_decider_config.json": b"{}", "w/run7/lora/a.bin": b"y" * 10,
                   "w/m.tar": tar.read_bytes()})
    monkeypatch.setattr(sources, "_s3_client", lambda opts: fake)
    path, info = sources.resolve("s3://bkt/w/run7/", log=lambda *_: None)
    assert os.path.exists(os.path.join(path, "lora", "a.bin")) and info["objects"] == 2
    path, info = sources.resolve("s3://bkt/w/m.tar", log=lambda *_: None)
    assert path.endswith("ckpt") and info["kind"] == "s3"


def test_remote_runs_get_presigned_urls_never_credentials(monkeypatch, tmp_path):
    monkeypatch.setattr(sources, "_s3_client", lambda opts: FakeS3({}))
    lab = tmp_path / "lab.yaml"
    lab.write_text(yaml.safe_dump({"name": "x", "models": {
        "a": {"serve": "s3://bkt/w/m.tar", "sha256": "ab", "profile": "secret-profile"},
        "b": {"serve": "hf://org/m@" + "c" * 40}},
        "finetune": {"ft": {"from": "s3://bkt/w/base.tar", "train": "t.jsonl"}}}))
    out = base.presign_sources(str(lab), log=lambda *_: None)
    remote = yaml.safe_load(open(out))
    assert remote["models"]["a"]["serve"].startswith("https://bkt.s3.amazonaws.com/w/m.tar?")
    assert "profile" not in remote["models"]["a"] and remote["models"]["a"]["sha256"] == "ab"
    assert remote["models"]["b"]["serve"].startswith("hf://")
    assert remote["finetune"]["ft"]["from"].startswith("https://")
    lab.write_text(yaml.safe_dump({"name": "x", "models": {"a": {"serve": "s3://bkt/prefix/"}}}))
    with pytest.raises(ValueError, match="not a prefix"):
        base.presign_sources(str(lab), log=lambda *_: None)
    lab.write_text(yaml.safe_dump({"name": "x", "models": {"a": "uniform"}}))
    assert base.presign_sources(str(lab), log=lambda *_: None) is None
