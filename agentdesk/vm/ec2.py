from __future__ import annotations
from typing import List, Optional

import boto3
from boto3.resources.base import ServiceResource
from mypy_boto3_ec2.service_resource import EC2ServiceResource, Instance as EC2Instance
from namesgenerator import get_random_name

from .base import DesktopVM, DesktopProvider
from .img import JAMMY
from agentdesk.server.models import V1ProviderData
from agentdesk.util import find_ssh_public_key


class EC2Provider(DesktopProvider):
    """A VM provider using AWS EC2"""

    def __init__(self, region: str) -> None:
        self.region = region
        self.ec2: EC2ServiceResource = boto3.resource("ec2", region_name=region)

    def create(
        self,
        name: Optional[str] = None,
        image: Optional[str] = None,
        memory: int = 4,
        cpu: int = 2,
        disk: str = "30gb",
        tags: List[str] = None,
        reserve_ip: bool = False,
        ssh_key: Optional[str] = None,
    ) -> DesktopVM:
        if not name:
            name = get_random_name()
        if not image:
            image = JAMMY.ec2

        instance_type = self._choose_instance_type(cpu, memory)

        ssh_key = ssh_key or find_ssh_public_key()
        if not ssh_key:
            raise ValueError("SSH key not provided or found")

        self._upload_ssh_key(name, ssh_key)

        instances = self.ec2.create_instances(
            ImageId=image,
            MinCount=1,
            MaxCount=1,
            InstanceType=instance_type,
            BlockDeviceMappings=[
                {
                    "DeviceName": "/dev/sdh",
                    "Ebs": {"VolumeSize": int(disk[:-2])},
                }
            ],
            TagSpecifications=[
                {
                    "ResourceType": "instance",
                    "Tags": [{"Key": "Name", "Value": name}]
                    + [{"Key": tag, "Value": ""} for tag in (tags or [])],
                }
            ],
        )

        instance_id = instances[0].id
        instances[0].wait_until_running()

        if reserve_ip:
            eip = boto3.client("ec2", region_name=self.region).allocate_address(
                Domain="vpc"
            )
            boto3.client("ec2", region_name=self.region).associate_address(
                InstanceId=instance_id, AllocationId=eip["AllocationId"]
            )

        instance = self.ec2.Instance(instance_id)
        public_ip = instance.public_ip_address

        return DesktopVM(
            name=name,
            addr=public_ip,
            cpu=cpu,
            memory=memory,
            disk=disk,
            image=image,
            provider=self.to_data(),
        )

    def _choose_instance_type(self, cpu: int, memory: int) -> str:
        """
        Choose an EC2 instance type based on CPU and memory requirements.
        """
        # This is a simple mapping. Update it according to your needs.
        if cpu <= 2:
            if memory <= 4:
                return "t2.micro"
            elif memory <= 8:
                return "t2.small"
            else:
                return "t2.medium"
        elif cpu <= 4:
            if memory <= 16:
                return "t2.large"
            else:
                return "t2.xlarge"
        else:
            # Default to a larger instance for higher requirements
            return "t2.2xlarge"

    def _upload_ssh_key(self, key_name: str, public_key_material: str) -> None:
        """
        Uploads an SSH public key to AWS EC2.

        :param key_name: The name for the SSH key.
        :param public_key_material: The public key material.
        """
        ec2_client = boto3.client("ec2", region_name=self.region)
        ec2_client.import_key_pair(
            KeyName=key_name, PublicKeyMaterial=public_key_material
        )

    def delete(self, name: str) -> None:
        instance = self._get_instance_by_name(name)
        if instance:
            instance.terminate()
            instance.wait_until_terminated()
            DesktopVM.delete(name)

    def start(self, name: str) -> None:
        instance = self._get_instance_by_name(name)
        if instance:
            instance.start()
            instance.wait_until_running()

    def stop(self, name: str) -> None:
        instance = self._get_instance_by_name(name)
        if instance:
            instance.stop()
            instance.wait_until_stopped()

    def list(self) -> List[DesktopVM]:
        instances = self.ec2.instances.filter(
            Filters=[{"Name": "instance-state-name", "Values": ["running", "stopped"]}]
        )
        desktops = []
        for instance in instances:
            desktops.append(DesktopVM.load(instance.id))
        return desktops

    def get(self, name: str) -> Optional[DesktopVM]:
        instance = self._get_instance_by_name(name)
        if instance:
            return DesktopVM.load(instance.id)
        return None

    def to_data(self) -> V1ProviderData:
        return V1ProviderData(type="ec2", args={"region": self.region})

    @classmethod
    def from_data(cls, data: V1ProviderData) -> EC2Provider:
        return cls(data.args["region"])

    def _get_instance_by_name(self, name: str) -> Optional[EC2Instance]:
        instances = self.ec2.instances.filter(
            Filters=[{"Name": "tag:Name", "Values": [name]}]
        )
        return next((instance for instance in instances), None)

    def _get_root_device_size(self, instance: EC2Instance) -> str:
        for device in instance.block_device_mappings:
            if device.get("DeviceName") == instance.root_device_name:
                volume_id = device.get("Ebs", {}).get("VolumeId")
                if volume_id:
                    volume = boto3.resource("ec2").Volume(volume_id)
                    return f"{volume.size}gb"
        return "unknown"
