from __future__ import annotations
from typing import List, Optional

import boto3
from boto3.resources.base import ServiceResource
from mypy_boto3_ec2.service_resource import EC2ServiceResource, Instance as EC2Instance

from .base import Desktop, DesktopProvider
from agentdesk.server.models import V1ProviderData


class EC2Provider(DesktopProvider):
    """A VM provider using AWS EC2"""

    def __init__(self, region: str) -> None:
        self.region = region
        self.ec2: EC2ServiceResource = boto3.resource("ec2", region_name=region)

    def create(
        self,
        name: str,
        image: str,
        memory: int = 4,
        cpu: int = 2,
        disk: str = "30gb",
        tags: List[str] = None,
        reserve_ip: bool = False,
        ssh_key: Optional[str] = None,
    ) -> Desktop:
        instance_type = "t2.micro" if cpu == 2 else "t2.small"

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

        instance = self.ec2.Instance(instance_id)
        public_ip = instance.public_ip_address

        return Desktop(
            name=name,
            addr=public_ip,
            cpu=cpu,
            memory=memory,
            disk=disk,
            image=image,
            provider=self.to_data(),
        )

    def delete(self, name: str) -> None:
        instance = self._get_instance_by_name(name)
        if instance:
            instance.terminate()
            instance.wait_until_terminated()
            Desktop.delete(name)

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

    def list(self) -> List[Desktop]:
        instances = self.ec2.instances.filter(
            Filters=[{"Name": "instance-state-name", "Values": ["running", "stopped"]}]
        )
        desktops = []
        for instance in instances:
            desktops.append(Desktop.load(instance.id))
        return desktops

    def get(self, name: str) -> Optional[Desktop]:
        instance = self._get_instance_by_name(name)
        if instance:
            return Desktop.load(instance.id)
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
