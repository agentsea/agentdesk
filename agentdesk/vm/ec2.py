from __future__ import annotations
from typing import List, Optional, Dict, Any
import atexit
import time

import boto3
from mypy_boto3_ec2.service_resource import Instance as EC2Instance
from namesgenerator import get_random_name
from botocore.exceptions import ClientError
import requests

from .base import DesktopVM, DesktopProvider
from .img import JAMMY
from agentdesk.server.models import V1ProviderData
from agentdesk.util import find_ssh_public_key, find_open_port
from agentdesk.proxy import ensure_ssh_proxy, cleanup_proxy


class EC2Provider(DesktopProvider):
    """VM provider using AWS EC2"""

    def __init__(
        self,
        region: str,
        aws_access_key_id: Optional[str] = None,
        aws_secret_access_key: Optional[str] = None,
    ):
        """Initialize the AWS EC2 VM Provider with region and optional credentials."""
        self.region = region
        if aws_access_key_id and aws_secret_access_key:
            self.session = boto3.Session(
                aws_access_key_id=aws_access_key_id,
                aws_secret_access_key=aws_secret_access_key,
                region_name=self.region,
            )
        else:
            self.session = boto3.Session(region_name=self.region)

    def create(
        self,
        name: Optional[str] = None,
        image: Optional[str] = None,
        memory: int = 4,
        cpu: int = 2,
        disk: str = "30gb",
        tags: Optional[Dict[str, str]] = None,
        reserve_ip: bool = False,
        public_ssh_key: Optional[str] = None,
        private_ssh_key: Optional[str] = None,
        owner_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> DesktopVM:
        if not name:
            name = get_random_name(sep="-")

        if DesktopVM.name_exists(name):
            raise ValueError(f"VM name '{name}' already exists")

        if not image:
            # Dynamically select the latest custom AMI based on a naming pattern
            # custom_ami = self._get_latest_custom_ami()
            # if not custom_ami:
            #     raise ValueError("Custom AMI not found")
            # image = custom_ami
            image = JAMMY.ec2

        public_ssh_key = public_ssh_key or find_ssh_public_key()

        user_data = f"""#cloud-config
users:
  - name: agentsea
    ssh_authorized_keys:
      - {public_ssh_key}
    sudo: ['ALL=(ALL) NOPASSWD:ALL']
    groups: ['sudo']
    shell: /bin/bash
"""
        instance_type = self._choose_instance_type(cpu, memory)

        ssh_key_name = self._ensure_ssh_key(name, public_ssh_key)
        if not ssh_key_name:
            raise ValueError("SSH key name not provided or found")

        disk_size_gib = self._convert_disk_size_to_gib(disk)
        security_group_id = self._ensure_sg(
            "agentdesk-default", "agentdesk default vm sg"
        )

        tag_specifications = [
            {
                "ResourceType": "instance",
                "Tags": [
                    {"Key": "Name", "Value": name},
                    {"Key": "provisioner", "Value": "agentdesk"},
                ]
                + [{"Key": k, "Value": v} for k, v in (tags or {}).items()],
            }
        ]

        instances = self.ec2.create_instances(
            ImageId=image,
            MinCount=1,
            MaxCount=1,
            InstanceType=instance_type,
            KeyName=ssh_key_name,
            SecurityGroupIds=[security_group_id],
            BlockDeviceMappings=[
                {
                    "DeviceName": "/dev/sdh",
                    "Ebs": {"VolumeSize": disk_size_gib},
                }
            ],
            TagSpecifications=tag_specifications,
            UserData=user_data,
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

        # wait till agentd is ready
        self._wait_till_ready(public_ip, private_ssh_key=private_ssh_key)

        desktop = DesktopVM(
            name=name,
            id=instance_id,
            addr=public_ip,
            cpu=cpu,
            memory=memory,
            disk=disk,
            image=image,
            provider=self.to_data(),
            requires_proxy=True,
            owner_id=owner_id,
            metadata=metadata,
            ssh_key=public_ssh_key,
        )

        print(f"\nsuccessfully created desktop '{name}'")
        return desktop

    def _wait_till_ready(
        self,
        addr: str,
        local_agentd_port: Optional[int] = None,
        private_ssh_key: Optional[str] = None,
    ) -> None:
        if not local_agentd_port:
            local_agentd_port = find_open_port(8000, 9000)
        print("waiting for desktop to be ready...")

        ready = False
        while not ready:
            print("waiting for desktop to be ready...")
            time.sleep(3)
            try:
                print("ensuring up ssh proxy...")
                pid = ensure_ssh_proxy(
                    local_port=local_agentd_port,
                    remote_port=8000,
                    ssh_host=addr,
                    ssh_key=private_ssh_key,
                )
                atexit.register(cleanup_proxy, pid)

                print("calling agentd...")
                response = requests.get(f"http://localhost:{local_agentd_port}/health")
                print("agentd response: ", response)
                if response.status_code == 200:
                    ready = True
                cleanup_proxy(pid)
                atexit.unregister(cleanup_proxy)
            except Exception:
                try:
                    cleanup_proxy(pid)
                except Exception:
                    pass

        print("cleaning up tunnel")
        cleanup_proxy(pid)
        atexit.unregister(cleanup_proxy)

    def _ensure_sg(self, name: str, description: str) -> str:
        # Attempt to find the default VPC
        vpcs = self.ec2_client.describe_vpcs(
            Filters=[{"Name": "isDefault", "Values": ["true"]}]
        )
        if not vpcs["Vpcs"]:
            raise Exception("No default VPC found in this region.")
        default_vpc_id = vpcs["Vpcs"][0]["VpcId"]

        # Check if the security group already exists
        try:
            security_groups = self.ec2_client.describe_security_groups(
                Filters=[
                    {"Name": "group-name", "Values": [name]},
                    {"Name": "vpc-id", "Values": [default_vpc_id]},
                ]
            )
            if security_groups["SecurityGroups"]:
                # Security group already exists
                return security_groups["SecurityGroups"][0]["GroupId"]
        except ClientError as e:
            print(f"Error checking for existing security group: {e}")

        # Security group does not exist, create it
        try:
            response = self.ec2_client.create_security_group(
                GroupName=name, Description=description, VpcId=default_vpc_id
            )
            security_group_id = response["GroupId"]

            # Add inbound rules
            self.ec2_client.authorize_security_group_ingress(
                GroupId=security_group_id,
                IpPermissions=[
                    {
                        "IpProtocol": "tcp",
                        "FromPort": 22,
                        "ToPort": 22,
                        "IpRanges": [{"CidrIp": "0.0.0.0/0"}],
                    },
                ],
            )
            return security_group_id
        except ClientError as e:
            raise Exception(f"Failed to create security group: {e}")

    def _convert_disk_size_to_gib(self, disk_size: str) -> int:
        """
        Converts a disk size specification string with units (e.g., "30gb", "1tb")
        to an integer representing the disk size in GiB.

        :param disk_size: Disk size string with units.
        :return: Disk size in GiB as an integer.
        """
        unit = disk_size[-2:].lower()
        size = int(disk_size[:-2])
        if unit == "gb":
            return size  # Assuming input in GiB, direct conversion for simplicity
        elif unit == "tb":
            return size * 1024  # Convert TB to GiB
        else:
            raise ValueError(f"Unsupported disk size unit: {unit}")

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

    def _ensure_ssh_key(self, key_name: str, public_key_material: str) -> str:
        """
        Uploads an SSH public key to AWS EC2, if it does not already exist.
        """
        ec2_client = boto3.client("ec2", region_name=self.region)
        try:
            ec2_client.describe_key_pairs(KeyNames=[key_name])
            print(f"Key pair '{key_name}' already exists. Skipping import.")
            return key_name
        except ec2_client.exceptions.ClientError as e:
            if "InvalidKeyPair.NotFound" in str(e):
                ec2_client.import_key_pair(
                    KeyName=key_name, PublicKeyMaterial=public_key_material
                )
                print(f"Key pair '{key_name}' successfully imported.")
                return key_name
            raise

    def _get_latest_custom_ami(self) -> Optional[str]:
        """
        Find the latest custom AMI based on a specific naming pattern.

        Returns:
            The AMI ID of the latest custom AMI if found, otherwise None.
        """
        ec2_client = boto3.client("ec2", region_name=self.region)
        filters = [
            {"Name": "name", "Values": ["agentd-ubuntu-22.04-*"]},
            {
                "Name": "owner-id",
                "Values": ["596381348884"],
            },  # Ubuntu's owner ID, adjust if your AMI has a different owner
        ]
        # Describe images with the specified filters
        response = ec2_client.describe_images(Filters=filters)

        images = response.get("Images", [])
        if not images:
            return None

        # Sort images by creation date in descending order
        sorted_images = sorted(images, key=lambda x: x["CreationDate"], reverse=True)

        # Return the AMI ID of the latest image
        latest_ami = sorted_images[0]["ImageId"]
        return latest_ami

    def _release_eip(self, instance: EC2Instance) -> None:
        # Assuming you have tagged your EIPs or have a way to associate them with instances
        filters = [{"Name": "instance-id", "Values": [instance.id]}]
        addresses = self.ec2_client.describe_addresses(Filters=filters)
        for address in addresses.get("Addresses", []):
            self.ec2_client.release_address(AllocationId=address["AllocationId"])
            print(f"Released EIP: {address['PublicIp']}")

    def _delete_ssh_key(self, name: str) -> None:

        try:
            self.ec2_client.delete_key_pair(KeyName=name)
            print(f"Deleted SSH key: {name}")
        except self.ec2_client.exceptions.ClientError as e:
            print(f"Failed to delete SSH key {name}: {e}")

    def delete(self, name: str) -> None:
        instance = self._get_instance_by_name(name)
        if instance:
            # Release EIP if reserved for the instance
            # self._release_eip(instance) # TODO

            # Terminate the instance
            instance.terminate()
            instance.wait_until_terminated()
            print("Remote instance terminated")

            # TODO: for now we always create the key
            self._delete_ssh_key(name)

            # Remove the desktop VM from local state
            desk = DesktopVM.get(name)
            if not desk:
                raise ValueError(
                    f"Desktop '{name}' not found in state, but deleted from provider"
                )
            desk.remove()

    def start(self, name: str, private_ssh_key: Optional[str] = None) -> None:
        desk = DesktopVM.get(name)
        if not desk:
            raise ValueError(f"Desktop {name} not found")
        instance = self._get_instance_by_name(name)
        if instance:
            instance.start()
            instance.wait_until_running()

        public_ip = instance.public_ip_address
        self._wait_till_ready(public_ip, private_ssh_key=private_ssh_key)
        desk.addr = public_ip
        desk.status = "running"
        desk.save()

    def stop(self, name: str) -> None:
        desk = DesktopVM.get(name)
        if not desk:
            raise ValueError(f"Desktop {name} not found")
        instance = self._get_instance_by_name(name)
        if instance:
            instance.stop()
            instance.wait_until_stopped()
        desk.status = "stopped"
        desk.save()

    def list_remote(self) -> List[DesktopVM]:
        instances = self.ec2.instances.filter(
            Filters=[{"Name": "instance-state-name", "Values": ["running", "stopped"]}]
        )
        desktops = []
        for instance in instances:
            desktops.append(DesktopVM.load(instance.id))
        return desktops

    def list(self) -> List[DesktopVM]:
        return DesktopVM.find()

    def get_remote(self, name: str) -> Optional[DesktopVM]:
        instance = self._get_instance_by_name(name)
        return DesktopVM.load(instance.id)

    def get(self, name: str) -> Optional[DesktopVM]:
        return DesktopVM.get(name)

    def to_data(self) -> V1ProviderData:
        provider = V1ProviderData(type="ec2")
        if self.region:
            provider.args = {"region": self.region}
        return provider

    @classmethod
    def from_data(cls, data: V1ProviderData) -> EC2Provider:
        if data.args and "region" in data.args:
            return cls(data.args["region"])
        return cls()

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

    def refresh(self, log: bool = True) -> None:
        """Refresh state"""
        for vm in DesktopVM.find():
            if vm.provider.type != "ec2":
                continue

            instance = self._get_instance_by_name(vm.name)
            if not instance:
                if log:
                    print(f"removing vm '{vm.name}' from state")
                vm.remove()
                return

            if not vm.reserved_ip:
                if vm.addr != instance.public_ip_address:
                    if log:
                        print(f"updating vm '{vm.name}' state")
                    vm.addr = instance.public_ip_address
                    vm.save()
                    return
