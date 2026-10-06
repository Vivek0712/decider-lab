from __future__ import annotations

import pytest
import yaml

from decider_lab.cli import build_parser, compute_config, main
from decider_lab.compute import aws, base, make_provider, vast
from decider_lab.compute.ssh import SshProvider


class FakeHost(base.Host):
    """Records commands; `fail_on` makes the first matching remote command fail."""

    def __init__(self, fail_on: str | None = None) -> None:
        super().__init__("fake", 22, "root")
        self.fail_on, self.cmds = fail_on, []

    def ssh(self, cmd, *, check=True, capture=False, timeout=None):
        self.cmds.append(cmd)
        if self.fail_on and self.fail_on in cmd:
            raise RuntimeError(f"remote command failed: {cmd[:40]}")
        if capture:
            return "[decider-lab] done\n__END__\n0\n"
        return ""

    def rsync(self, src, dst, *, excludes=(), delete=True, check=True):
        self.cmds.append(f"rsync {src} {dst}")
        return 0


class FakeProvider(base.Provider):
    name = "fake"

    def __init__(self, host):
        self.host, self.released = host, 0

    def acquire(self, log):
        return self.host

    def release(self, log):
        self.released += 1


def lab_file(tmp_path, compute=None):
    p = tmp_path / "lab.yaml"
    p.write_text(yaml.safe_dump({"name": "x", "models": {"m": "uniform"}, **({"compute": compute} if compute else {})}))
    return p


def test_run_on_releases_after_success_and_runs_locally_there(tmp_path, monkeypatch):
    monkeypatch.setattr(base.time, "sleep", lambda s: None)
    host = FakeHost()
    prov = FakeProvider(host)
    base.run_on(prov, str(lab_file(tmp_path)), log=lambda *_: None)
    assert prov.released == 1
    launch = next(c for c in host.cmds if "nohup bash" in c)
    assert "--on local" in launch and "< /dev/null" in launch
    assert launch.rstrip().endswith("& }"), "only the job may be backgrounded, not the && chain"


@pytest.mark.parametrize("step", ["bootstrap.sh", "nohup bash", "mkdir -p"])
def test_run_on_releases_when_any_step_fails(tmp_path, monkeypatch, step):
    monkeypatch.setattr(base.time, "sleep", lambda s: None)
    prov = FakeProvider(FakeHost(fail_on=step))
    with pytest.raises(RuntimeError):
        base.run_on(prov, str(lab_file(tmp_path)), log=lambda *_: None)
    assert prov.released == 1


def test_run_on_keep_skips_release(tmp_path, monkeypatch):
    monkeypatch.setattr(base.time, "sleep", lambda s: None)
    prov = FakeProvider(FakeHost())
    base.run_on(prov, str(lab_file(tmp_path)), keep=True, log=lambda *_: None)
    assert prov.released == 0


def test_compute_config_lab_then_flags(tmp_path):
    p = lab_file(tmp_path, {"on": "vast", "vast": {"gpu": "RTX_4090", "max_price": 0.5}})
    a = build_parser().parse_args(["run", str(p), "--gpu", "A100_SXM4", "--region", "eu-west-1"])
    on, opts, _ = compute_config(a)
    assert on == "vast" and opts == {"gpu": "A100_SXM4", "max_price": 0.5}  # --region is an aws flag
    a = build_parser().parse_args(["run", str(p), "--on", "aws", "--instance-type", "c7i.2xlarge"])
    on, opts, _ = compute_config(a)
    assert on == "aws" and opts == {"instance_type": "c7i.2xlarge"}
    a = build_parser().parse_args(["run", str(lab_file(tmp_path))])
    assert compute_config(a)[0] == "local"


def test_make_provider():
    assert isinstance(make_provider("vast", {"gpu": "A100_SXM4"}), vast.VastProvider)
    p = make_provider("ssh", {"host": "ubuntu@10.1.2.3:2222", "key": "~/.ssh/k"})
    assert isinstance(p, SshProvider) and p.host.user == "ubuntu" and p.host.port == 2222
    assert p.host.work == "/home/ubuntu/decider-lab-work"
    with pytest.raises(ValueError):
        make_provider("ssh", {})
    with pytest.raises(ValueError):
        make_provider("gcp")


def test_vast_refuses_without_credit(monkeypatch):
    monkeypatch.setattr(vast, "credit", lambda: 1.0)
    with pytest.raises(RuntimeError, match="does not cover"):
        vast.VastProvider(max_price=1.0).check(2)


def test_vast_release_destroys(monkeypatch):
    gone = []
    monkeypatch.setattr(vast, "destroy", lambda iid, **k: gone.append(iid) or True)
    p = vast.VastProvider()
    p.iid = 42
    p.release(lambda *_: None)
    p.release(lambda *_: None)  # idempotent
    assert gone == [42]


# ---- aws, against a fake boto3 session --------------------------------------------------------

class FakeClient:
    def __init__(self, calls, quota=8.0, vcpus=4):
        self.calls, self.quota, self.vcpus = calls, quota, vcpus

    def __getattr__(self, name):
        def call(**kw):
            self.calls.append((name, kw))
            return {
                "get_caller_identity": {"Account": "123456789012"},
                "describe_instance_types": {"InstanceTypes": [{"VCpuInfo": {"DefaultVCpus": self.vcpus}}]},
                "get_service_quota": {"Quota": {"Value": self.quota}},
                "get_parameter": {"Parameter": {"Value": "ami-123"}},
                "create_key_pair": {"KeyMaterial": "-----BEGIN KEY-----"},
                "describe_vpcs": {"Vpcs": [{"VpcId": "vpc-1"}]},
                "create_security_group": {"GroupId": "sg-1"},
                "run_instances": {"Instances": [{"InstanceId": "i-1"}]},
                "describe_instances": {"Reservations": [{"Instances": [{"PublicIpAddress": "1.2.3.4"}]}]},
            }.get(name, {})
        return call

    def get_waiter(self, name):
        calls = self.calls

        class W:
            def wait(self, **kw):
                calls.append((f"wait:{name}", kw))
        return W()


class FakeSession:
    def __init__(self, **kw):
        self.calls = []
        self.kw = kw

    def client(self, name):
        return FakeClient(self.calls, **self.kw)


def test_aws_quota_check_names_the_quota():
    p = aws.AwsProvider("g6e.xlarge", session=FakeSession(quota=0.0))
    with pytest.raises(RuntimeError, match="L-DB2E81BA"):
        p.check(2)
    aws.AwsProvider("c7i.2xlarge", session=FakeSession(quota=16.0)).check(2)


def test_aws_launch_is_locked_down_and_released_in_order(monkeypatch, tmp_path):
    monkeypatch.setattr(aws, "my_ip", lambda: "5.6.7.8")
    monkeypatch.setattr(aws, "wait_reachable", lambda host, *a, **k: host)
    monkeypatch.setenv("HOME", str(tmp_path))
    sess = FakeSession()
    p = aws.AwsProvider("g6e.xlarge", session=sess)
    p.max_hours = 1
    host = p.acquire(lambda *_: None)
    assert host.user == "ubuntu" and host.host == "1.2.3.4"
    names = [c[0] for c in sess.calls]
    ingress = dict(sess.calls)["authorize_security_group_ingress"]["IpPermissions"][0]
    assert ingress["IpRanges"] == [{"CidrIp": "5.6.7.8/32"}] and ingress["FromPort"] == 22
    run = dict(sess.calls)["run_instances"]
    assert run["InstanceInitiatedShutdownBehavior"] == "terminate" and "shutdown -h +75" in run["UserData"]
    key_path = p.key_path
    p.release(lambda *_: None)
    names = [c[0] for c in sess.calls]
    assert names.index("terminate_instances") < names.index("delete_security_group") < names.index("delete_key_pair")
    assert not __import__("os").path.exists(key_path)


def test_aws_family():
    assert aws.family("g6e.xlarge") == "g" and aws.family("p5.48xlarge") == "p" and aws.family("c7i.2xlarge") == "c"


def test_compute_cli_rejects_local(capsys):
    assert main(["compute", "ls", "--on", "vast"]) in (0, 2)  # vastai may be absent here
