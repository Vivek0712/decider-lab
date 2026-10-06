"""AWS EC2: launch an instance for the run, terminate it afterwards.

    compute:
      on: aws
      aws: {instance_type: g6e.xlarge, region: us-east-1, profile: my-profile, disk_gb: 150}

What it creates, all tagged `decider-lab=<lab>`, and removes again:
  - a key pair (the private key in ~/.cache/decider-lab/keys, mode 600, deleted afterwards);
  - a security group in the default VPC allowing ssh from this machine's public IP only;
  - the instance, from the Deep Learning Base GPU AMI (Ubuntu 22.04, NVIDIA driver) for GPU
    types, or plain Ubuntu 22.04 for CPU types (found through public SSM parameters).

Safety net: the instance is launched with shutdown-behaviour `terminate` and schedules its own
shutdown at max_hours + 15 minutes, so it terminates even if this process or laptop dies.

Before launching, it checks credentials and the vCPU quota of the instance family (new accounts
often have 0 vCPU for G and P instances; the error names the quota to request).
Needs: pip install "decider-lab[aws]" (boto3), and credentials (profile, env or SSO).
"""

from __future__ import annotations

import os
import time
import urllib.request
from typing import Any

from .base import CPU_STRANDS, DEFAULT_STRANDS, Host, Provider, wait_reachable

TAG = "decider-lab"
GPU_AMI = "/aws/service/deeplearning/ami/x86_64/base-oss-nvidia-driver-gpu-ubuntu-22.04/latest/ami-id"
CPU_AMI = "/aws/service/canonical/ubuntu/server/22.04/stable/current/amd64/hvm/ebs-gp2/ami-id"
# Running On-Demand instance vCPU quotas, by instance family letter
QUOTAS = {"g": ("L-DB2E81BA", "Running On-Demand G and VT instances"),
          "p": ("L-417A185B", "Running On-Demand P instances")}
STANDARD = ("L-1216C47A", "Running On-Demand Standard (A, C, D, H, I, M, R, T, Z) instances")
GPU_FAMILIES = ("g", "p")


def _boto(profile: str | None, region: str) -> Any:
    try:
        import boto3
    except ImportError as e:
        raise ImportError("the aws backend needs boto3: pip install 'decider-lab[aws]'") from e
    return boto3.Session(profile_name=profile, region_name=region)


def family(instance_type: str) -> str:
    """The family letter that decides the vCPU quota: g6e.xlarge -> g, p5.48xlarge -> p, c7i.2xlarge -> c."""
    return instance_type[:1].lower()


def my_ip() -> str:
    with urllib.request.urlopen("https://checkip.amazonaws.com", timeout=10) as r:
        return r.read().decode().strip()


class AwsProvider(Provider):
    name = "aws"

    def __init__(self, instance_type: str = "g6e.xlarge", region: str = "us-east-1", profile: str | None = None,
                 disk_gb: int = 150, ami: str | None = None, subnet_id: str | None = None, label: str = "lab",
                 boot_timeout: float = 900, session: Any = None) -> None:
        self.instance_type, self.region, self.profile, self.disk_gb = instance_type, region, profile, disk_gb
        self.ami, self.subnet_id, self.label, self.boot_timeout = ami, subnet_id, label, boot_timeout
        self.session = session
        self.gpu = family(instance_type) in GPU_FAMILIES
        self.instance_id: str | None = None
        self.sg_id: str | None = None
        self.key_name: str | None = None
        self.key_path: str | None = None
        self.max_hours = 2.0

    @property
    def ec2(self) -> Any:
        if self.session is None:
            self.session = _boto(self.profile, self.region)
        return self.session.client("ec2")

    def default_strands_spec(self) -> str:
        return DEFAULT_STRANDS if self.gpu else CPU_STRANDS

    def check(self, max_hours: float) -> None:
        self.max_hours = max_hours
        ident = self.session_or_new().client("sts").get_caller_identity()
        types = self.ec2.describe_instance_types(InstanceTypes=[self.instance_type])["InstanceTypes"]
        if not types:
            raise RuntimeError(f"{self.instance_type} is not offered in {self.region}")
        vcpus = int(types[0]["VCpuInfo"]["DefaultVCpus"])
        code, qname = QUOTAS.get(family(self.instance_type), STANDARD)
        try:
            q = self.session_or_new().client("service-quotas").get_service_quota(ServiceCode="ec2", QuotaCode=code)
            limit = float(q["Quota"]["Value"])
        except Exception:  # quota API not allowed for this role: let the launch say so instead
            limit = float("inf")
        if limit < vcpus:
            raise RuntimeError(
                f"account {ident['Account']} has a '{qname}' quota of {limit:.0f} vCPU in {self.region}; "
                f"{self.instance_type} needs {vcpus}. Request an increase (Service Quotas > EC2 > {code}), "
                "or pick another backend (vast, ssh) or a CPU instance type for evaluation-only labs.")

    def session_or_new(self) -> Any:
        if self.session is None:
            self.session = _boto(self.profile, self.region)
        return self.session

    def _tags(self, kind: str) -> list[dict[str, Any]]:
        return [{"ResourceType": kind, "Tags": [{"Key": TAG, "Value": self.label},
                                               {"Key": "Name", "Value": f"decider-lab-{self.label}"}]}]

    def acquire(self, log: Any) -> Host:
        ec2 = self.ec2
        stamp = time.strftime("%Y%m%d-%H%M%S")
        ami = self.ami or self.session_or_new().client("ssm").get_parameter(
            Name=GPU_AMI if self.gpu else CPU_AMI)["Parameter"]["Value"]
        # key pair, kept locally only for this run
        self.key_name = f"decider-lab-{self.label}-{stamp}"
        key = ec2.create_key_pair(KeyName=self.key_name, KeyType="ed25519", TagSpecifications=self._tags("key-pair"))
        keys_dir = os.path.join(os.path.expanduser("~"), ".cache", "decider-lab", "keys")
        os.makedirs(keys_dir, exist_ok=True)
        self.key_path = os.path.join(keys_dir, self.key_name + ".pem")
        with open(os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
            fh.write(key["KeyMaterial"])
        # ssh from this machine only
        vpc = None
        if self.subnet_id:
            vpc = ec2.describe_subnets(SubnetIds=[self.subnet_id])["Subnets"][0]["VpcId"]
        else:
            vpcs = ec2.describe_vpcs(Filters=[{"Name": "is-default", "Values": ["true"]}])["Vpcs"]
            if not vpcs:
                raise RuntimeError(f"no default VPC in {self.region}; set aws.subnet_id")
            vpc = vpcs[0]["VpcId"]
        self.sg_id = ec2.create_security_group(GroupName=self.key_name, Description="decider-lab ssh, one run",
                                               VpcId=vpc, TagSpecifications=self._tags("security-group"))["GroupId"]
        ip = my_ip()
        ec2.authorize_security_group_ingress(GroupId=self.sg_id, IpPermissions=[{
            "IpProtocol": "tcp", "FromPort": 22, "ToPort": 22, "IpRanges": [{"CidrIp": f"{ip}/32"}]}])
        minutes = int(self.max_hours * 60) + 15
        user_data = f"#!/bin/bash\nshutdown -h +{minutes} 'decider-lab max_hours reached'\n"
        net: dict[str, Any] = {"DeviceIndex": 0, "AssociatePublicIpAddress": True, "Groups": [self.sg_id]}
        if self.subnet_id:
            net["SubnetId"] = self.subnet_id
        res = ec2.run_instances(
            ImageId=ami, InstanceType=self.instance_type, MinCount=1, MaxCount=1, KeyName=self.key_name,
            NetworkInterfaces=[net], UserData=user_data, InstanceInitiatedShutdownBehavior="terminate",
            BlockDeviceMappings=[{"DeviceName": "/dev/sda1",
                                  "Ebs": {"VolumeSize": self.disk_gb, "VolumeType": "gp3", "DeleteOnTermination": True}}],
            TagSpecifications=self._tags("instance") + self._tags("volume"),
            MetadataOptions={"HttpTokens": "required"})
        self.instance_id = res["Instances"][0]["InstanceId"]
        log(f"[aws] {self.instance_type} {self.instance_id} from {ami} in {self.region} (self-terminates after "
            f"{minutes} min); ssh allowed from {ip} only")
        ec2.get_waiter("instance_running").wait(InstanceIds=[self.instance_id])
        desc = ec2.describe_instances(InstanceIds=[self.instance_id])["Reservations"][0]["Instances"][0]
        address = desc.get("PublicIpAddress") or desc.get("PrivateIpAddress")
        host = Host(address, 22, "ubuntu", self.key_path)
        return wait_reachable(host, self.boot_timeout, f"EC2 instance {self.instance_id}")

    def release(self, log: Any) -> None:
        ec2 = self.ec2
        if self.instance_id:
            try:
                ec2.terminate_instances(InstanceIds=[self.instance_id])
                ec2.get_waiter("instance_terminated").wait(InstanceIds=[self.instance_id])
                log(f"[aws] instance {self.instance_id} terminated")
                self.instance_id = None
            except Exception as e:
                log(f"[aws] WARNING: could not confirm {self.instance_id} terminated ({e}); it also terminates "
                    f"itself at max_hours + 15 min. Check: aws ec2 describe-instances --instance-ids {self.instance_id}")
        if self.sg_id and not self.instance_id:
            for _ in range(10):  # the group stays in use for a moment after termination
                try:
                    ec2.delete_security_group(GroupId=self.sg_id)
                    self.sg_id = None
                    break
                except Exception:
                    time.sleep(10)
        if self.key_name:
            try:
                ec2.delete_key_pair(KeyName=self.key_name)
            except Exception as e:
                log(f"[aws] could not delete key pair {self.key_name}: {e}")
            self.key_name = None
        if self.key_path and os.path.exists(self.key_path):
            os.remove(self.key_path)
        if self.sg_id:
            log(f"[aws] security group {self.sg_id} left behind; delete it with: "
                f"aws ec2 delete-security-group --group-id {self.sg_id}")


def tagged_instances(region: str, profile: str | None) -> list[dict[str, Any]]:
    ec2 = _boto(profile, region).client("ec2")
    out = []
    for r in ec2.describe_instances(Filters=[{"Name": "tag-key", "Values": [TAG]},
                                             {"Name": "instance-state-name",
                                              "Values": ["pending", "running", "stopping", "stopped"]}])["Reservations"]:
        out += r["Instances"]
    return out
