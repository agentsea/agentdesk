from __future__ import annotations
from typing import List, Optional, Dict, Any
import atexit
import time
import logging

import boto3
from mypy_boto3_ec2.service_resource import Instance as EC2Instance
from mypy_boto3_ec2 import EC2Client, EC2ServiceResource
from namesgenerator import get_random_name
from botocore.exceptions import ClientError
import requests

from .base import DesktopInstance, DesktopProvider
from .img import JAMMY
from agentdesk.server.models import V1ProviderData
from agentdesk.util import find_open_port, generate_short_hash, generate_random_string
from agentdesk.proxy import ensure_ssh_proxy, cleanup_proxy
from agentdesk.key import SSHKeyPair


logger = logging.getLogger(__name__)


class EC2Provider(DesktopProvider):
    """VM provider using AWS EC2"""

    AVAILABLE_REGIONS = {
        "us-east-1",
        "us-west-1",
        "us-west-2",
        "eu-west-1",
        "eu-central-1",
        "ap-southeast-1",
        "ap-northeast-1",
    }

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

        self.ec2: EC2ServiceResource = self.session.resource(
            "ec2", region_name=self.region
        )  # type: ignore
        self.ec2_client: EC2Client = self.session.client("ec2", region_name=self.region)  # type: ignore

    def create(
        self,
        name: Optional[str] = None,
        image: Optional[str] = None,
        memory: int = 4,
        cpu: int = 2,
        disk: str = "30gb",
        tags: Optional[Dict[str, str]] = None,
        reserve_ip: bool = False,
        ssh_key_pair: Optional[str] = None,
        owner_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        generate_password: bool = False,
        sub_folder: Optional[str] = None,
        id: Optional[str] = None,
    ) -> DesktopInstance:
        if id:
            raise ValueError("cannot set id for ec2 provider")

        if generate_password:
            raise NotImplementedError(
                "generate_password not implemented for ec2 provider"
            )
        if sub_folder:
            raise NotImplementedError("sub_folder not implemented for ec2 provider")
        if not name:
            name = get_random_name(sep="-")
            if not name:
                raise ValueError("could not generate name")

        if DesktopInstance.name_exists(name):
            raise ValueError(f"VM name '{name}' already exists")

        if not image:
            image = self._get_ami_id_by_name(JAMMY.ec2)  # type: ignore

        if not ssh_key_pair:
            key_pair = SSHKeyPair.generate_key(
                f"{name}-{generate_short_hash(generate_random_string())}",
                owner_id or "local",
                metadata={"generated_for": name},
            )
            public_ssh_key = key_pair.public_key
            private_ssh_key = key_pair.decrypt_private_key(key_pair.private_key)
        else:
            key_pairs = SSHKeyPair.find(name=ssh_key_pair, owner_id=owner_id or "local")
            if not key_pairs:
                raise ValueError(f"SSH key pair '{ssh_key_pair}' not found")
            key_pair = key_pairs[0]

        public_ssh_key = key_pair.public_key
        private_ssh_key = key_pair.decrypt_private_key(key_pair.private_key)

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
                    {"Key": "Owner", "Value": owner_id or "local"},
                    {"Key": "provisioner", "Value": "agentdesk"},
                ]
                + [{"Key": k, "Value": v} for k, v in (tags or {}).items()],
            }
        ]

        instances = self.ec2.create_instances(
            ImageId=image,  # type: ignore
            MinCount=1,
            MaxCount=1,
            InstanceType=instance_type,  # type: ignore
            KeyName=ssh_key_name,
            SecurityGroupIds=[security_group_id],
            BlockDeviceMappings=[
                {
                    "DeviceName": "/dev/sdh",
                    "Ebs": {"VolumeSize": disk_size_gib},
                }
            ],
            TagSpecifications=tag_specifications,  # type: ignore
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

        desktop = DesktopInstance(
            name=name,
            id=instance_id,
            addr=public_ip,
            cpu=cpu,
            memory=memory,  # type: ignore
            disk=disk,
            image=image,
            provider=self.to_data(),
            requires_proxy=True,
            owner_id=owner_id,
            metadata=metadata,
            key_pair_name=key_pair.name,
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
            if not local_agentd_port:
                raise ValueError("could not find open port")
        print("waiting for desktop to be ready...")

        ready = False
        while not ready:
            print("waiting for desktop to be ready...")
            time.sleep(3)
            try:
                logger.debug("ensuring up ssh proxy...")
                pid = ensure_ssh_proxy(
                    local_port=local_agentd_port,
                    remote_port=8000,
                    ssh_host=addr,
                    ssh_key=private_ssh_key,
                    log_error=False,
                )
                atexit.register(cleanup_proxy, pid)

                logger.debug("calling agentd...")
                response = requests.get(f"http://localhost:{local_agentd_port}/health")
                logger.debug(f"agentd response: {response}")
                if response.status_code == 200:
                    ready = True
                cleanup_proxy(pid)
                atexit.unregister(cleanup_proxy)
            except Exception:
                try:
                    cleanup_proxy(pid, log_error=False)  # type: ignore
                except Exception:
                    pass

        logger.debug("cleaning up tunnel")
        try:
            cleanup_proxy(pid)  # type: ignore
            atexit.unregister(cleanup_proxy)
        except Exception:
            pass

    def _ensure_sg(self, name: str, description: str) -> str:
        # Attempt to find the default VPC
        vpcs = self.ec2_client.describe_vpcs(
            Filters=[{"Name": "isDefault", "Values": ["true"]}]
        )
        if not vpcs["Vpcs"]:
            raise Exception("No default VPC found in this region.")
        default_vpc_id = vpcs["Vpcs"][0]["VpcId"]  # type: ignore

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
                return security_groups["SecurityGroups"][0]["GroupId"]  # type: ignore
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
                return "t2.medium"
            elif memory <= 8:
                return "t2.large"
            else:
                return "t2.medium"
        elif cpu <= 4:
            if memory <= 16:
                return "t2.xlarge"
            else:
                return "t2.2xlarge"
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

    def _get_ami_id_by_name(self, ami_name: str) -> str:
        """
        Find the latest custom AMI based on a specific naming pattern.

        Returns:
            The AMI ID of the latest custom AMI if found, otherwise None.
        """
        images = self.ec2_client.describe_images(
            Filters=[{"Name": "name", "Values": [ami_name]}]
        ).get("Images", [])
        if not images:
            raise ValueError(
                f"No images found with name: {ami_name} in region {self.region}"
            )
        return images[0]["ImageId"]  # type: ignore

    def _release_eip(self, instance: EC2Instance) -> None:
        # Assuming you have tagged your EIPs or have a way to associate them with instances
        filters = [{"Name": "instance-id", "Values": [instance.id]}]
        addresses = self.ec2_client.describe_addresses(Filters=filters)  # type: ignore
        for address in addresses.get("Addresses", []):
            self.ec2_client.release_address(AllocationId=address["AllocationId"])  # type: ignore
            print(f"Released EIP: {address['PublicIp']}")  # type: ignore

    def _delete_ssh_key(self, name: str) -> None:
        try:
            self.ec2_client.delete_key_pair(KeyName=name)
            print(f"Deleted SSH key: {name}")
        except self.ec2_client.exceptions.ClientError as e:
            print(f"Failed to delete SSH key {name}: {e}")

    def delete(self, name: str, owner_id: Optional[str] = None) -> None:
        instance = self._get_instance_by_name(name, owner_id=owner_id)
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
            desk = DesktopInstance.get(name)
            if not desk:
                raise ValueError(
                    f"Desktop '{name}' not found in state, but deleted from provider"
                )
            desk.remove()

        keys = SSHKeyPair.find(owner_id=owner_id or "local")
        if keys:
            for key in keys:
                if (
                    "generated_for" in key.metadata
                    and key.metadata["generated_for"] == name
                ):
                    key.delete(key.name, key.owner_id)
                    print(f"Deleted SSH key {key.name}")

    def start(
        self,
        name: str,
        private_ssh_key: Optional[str] = None,
        owner_id: Optional[str] = None,
    ) -> None:
        desk = DesktopInstance.get(name, owner_id=owner_id)
        if not desk:
            raise ValueError(f"Desktop {name} not found")
        instance = self._get_instance_by_name(name)
        if instance:
            instance.start()
            instance.wait_until_running()

        if not instance:
            raise ValueError("Instance not found")

        public_ip = instance.public_ip_address
        self._wait_till_ready(public_ip, private_ssh_key=private_ssh_key)
        desk.addr = public_ip
        desk.status = "running"
        desk.save()

    def stop(self, name: str, owner_id: Optional[str] = None) -> None:
        desk = DesktopInstance.get(name, owner_id)
        if not desk:
            raise ValueError(f"Desktop {name} not found")
        instance = self._get_instance_by_name(name)
        if instance:
            instance.stop()
            instance.wait_until_stopped()
        desk.status = "stopped"
        desk.save()

    def list_remote(self) -> List[DesktopInstance]:
        instances = self.ec2.instances.filter(
            Filters=[{"Name": "instance-state-name", "Values": ["running", "stopped"]}]
        )
        desktops = []
        for instance in instances:
            desktops.append(DesktopInstance.load(instance.id))
        return desktops

    def list(self) -> List[DesktopInstance]:
        return DesktopInstance.find()

    def get_remote(self, name: str) -> Optional[DesktopInstance]:
        instance = self._get_instance_by_name(name)
        if not instance:
            return None
        return DesktopInstance.load(instance.id)

    def get(
        self, name: str, owner_id: Optional[str] = None
    ) -> Optional[DesktopInstance]:
        return DesktopInstance.get(name, owner_id=owner_id)

    def to_data(self) -> V1ProviderData:
        provider = V1ProviderData(type="ec2")
        if self.region:
            provider.args = {"region": self.region}
        return provider

    @classmethod
    def from_data(cls, data: V1ProviderData) -> EC2Provider:
        if data.args and "region" in data.args:
            region = data.args["region"]
            if region not in cls.AVAILABLE_REGIONS:
                raise ValueError(
                    f"Invalid region: {region}. Available regions are: "
                    f"{', '.join(sorted(cls.AVAILABLE_REGIONS))}"
                )
            return cls(region)
        return cls("us-east-1")

    def _get_instance_by_name(
        self, name: str, owner_id: Optional[str] = None
    ) -> Optional[EC2Instance]:
        filters = [{"Name": "tag:Name", "Values": [name]}]

        if owner_id is not None:
            filters.append({"Name": "Owner", "Values": [owner_id]})

        instances = self.ec2.instances.filter(Filters=filters)  # type: ignore

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
        for vm in DesktopInstance.find():
            if not vm.provider:
                continue
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
