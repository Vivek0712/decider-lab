"""Fake cloud fixtures: deterministic, mutable in memory, no network."""

from __future__ import annotations

from decider_lab.ui.cloud_fake import FakeCloud, fake_cloud_enabled

NOW = 1791283200.0  # 2026-10-06T10:00:00Z


def test_enabled_flag():
    assert fake_cloud_enabled({"DECIDER_LAB_FAKE_CLOUD": "1"})
    assert not fake_cloud_enabled({"DECIDER_LAB_FAKE_CLOUD": "0"}) and not fake_cloud_enabled({})


def test_vast_fixtures():
    c = FakeCloud(now=NOW + 3600)
    assert c.vast_status()["credit_usd"] == 25.4
    offers = c.vast_offers(gpu="A100_SXM4", max_price=0.8)["items"]
    assert [o["id"] for o in offers] == [1234567, 1234568] and offers[0]["dph_total"] <= offers[1]["dph_total"]
    assert c.vast_offers(gpu="A100_SXM4", max_price=0.5)["items"] == []
    inst = c.vast_instances()["items"]
    assert len(inst) == 1 and inst[0]["id"] == 9876543 and inst[0]["idle"] is True and inst[0]["job_id"] is None
    assert inst[0]["uptime_s"] > 0 and inst[0]["cost_so_far_usd"] > 0
    assert c.vast_destroy(1) is None
    assert c.vast_destroy("9876543") == {"destroyed": True}
    assert c.vast_instances()["items"] == []
    c.reset()
    assert len(c.vast_instances()["items"]) == 1


def test_aws_fixtures():
    c = FakeCloud(now=NOW)
    assert c.aws_identity("heisenberg")["account"] == "123456789012"
    assert {q["family"] for q in c.aws_quotas()["items"]} == {"g", "p", "standard"}
    assert c.aws_instances()["items"][0]["id"] == "i-0abc123def4567890"
    assert c.aws_terminate("i-nope") is None
    assert c.aws_terminate_all() == {"terminating": ["i-0abc123def4567890"]}
    nova = c.bedrock_models(q="nova pro")["items"]
    assert [m["invoke_id"] for m in nova] == ["us.amazon.nova-pro-v1:0"]
    assert nova[0]["spec_yaml"] == "{bedrock: us.amazon.nova-pro-v1:0, region: us-east-1}"
    assert all(r["fake"] for r in (c.aws_profiles(), c.aws_quotas(), c.bedrock_models()))


def test_doctor_rows():
    rows = FakeCloud().doctor_rows()
    assert [r[1] for r in rows] == ["vast.ai", "aws"]
