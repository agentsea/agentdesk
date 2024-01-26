from __future__ import annotations
from typing import List, Optional
import re

from google.cloud import compute_v1

from .base import Desktop, DesktopProvider
from agentdesk.server.models import V1ProviderData


class GCEProvider(DesktopProvider):
    """A VM provider using GCE"""

    def __init__(
        self,
        project_id: Optional[str] = None,
        zone: Optional[str] = None,
        region: Optional[str] = None,
    ):
        """Initialize the GCP VM Provider with project and zone details."""
        self.project_id = project_id
        self.zone = zone
        self.region = region

    def reserve_static_ip(self, name: str) -> str:
        """Reserve a static external IP address."""
        addresses_client = compute_v1.AddressesClient()
        address = compute_v1.Address(name=name)

        operation = addresses_client.insert(
            project=self.project_id, region=self.region, address_resource=address
        )
        operation.result()

        reserved_address = addresses_client.get(
            project=self.project_id, region=self.region, address=name
        )
        return reserved_address.address

    def open_firewall(
        self, rule_name: str, ports: List[str], network: str = "global/networks/default"
    ):
        """Create a firewall rule to allow incoming traffic on specified ports."""
        firewall_client = compute_v1.FirewallsClient()
        firewall = compute_v1.Firewall()
        firewall.name = rule_name
        firewall.direction = compute_v1.Firewall.Direction.INGRESS
        firewall.allowed = [{"IPProtocol": "tcp", "ports": ports}]
        firewall.network = network

        operation = firewall_client.insert(
            project=self.project_id, firewall_resource=firewall
        )
        return operation.result()

    def create(
        self,
        name: str,
        image: str,
        memory: str = "4gb",
        cpu: int = 2,
        disk: str = "30gb",
        tags: List[str] = None,
        reserve_ip: bool = False,
    ) -> Desktop:
        """Create a VM in GCP."""

        bucket_name, image_file = self._parse_gcs_url(image)
        image_name = self._generate_image_name_from_gcs_url(image)

        images_client = compute_v1.ImagesClient()

        # Check if the image exists
        try:
            images_client.get(project=self.project_id, image=image_name)
        except Exception as e:
            # Image does not exist, load it from the GCS URL
            self._load_custom_image(
                project_id=self.project_id,
                image_name=image_name,
                bucket_name=bucket_name,
                image_file=image_file,
            )

        instance_client = compute_v1.InstancesClient()
        machine_type = (
            f"zones/{self.zone}/machineTypes/custom-{cpu}-{int(memory[:-2])*1024}"
        )

        disk_config = compute_v1.AttachedDiskInitializeParams(
            disk_size_gb=int(disk[:-2]), source_image=image
        )
        disk = compute_v1.AttachedDisk(
            boot=True, auto_delete=True, initialize_params=disk_config
        )
        network_interface = compute_v1.NetworkInterface(name="global/networks/default")

        instance = compute_v1.Instance(
            name=name,
            machine_type=machine_type,
            disks=[disk],
            network_interfaces=[network_interface],
        )

        if reserve_ip:
            static_ip_name = f"{name}-ip"
            reserved_ip = self.reserve_static_ip(static_ip_name)
            access_config = compute_v1.AccessConfig(
                nat_ip=reserved_ip, name="External NAT"
            )
            instance.network_interfaces[0].access_configs = [access_config]

        if tags:
            instance.labels = {tag: "" for tag in tags}

        operation = instance_client.insert(
            project=self.project_id, zone=self.zone, instance_resource=instance
        )
        operation.result()  # Wait for operation to complete

        created_instance = instance_client.get(
            project=self.project_id, zone=self.zone, instance=name
        )
        ip_address = created_instance.network_interfaces[0].access_configs[0].nat_ip

        new_desktop = Desktop(
            name=name,
            addr=ip_address,
            cpu=cpu,
            memory=memory,
            disk=disk,
            image=image,
            provider=self.to_data(),
        )

        return new_desktop

    def _parse_gcs_url(self, gcs_url: str) -> (str, str):
        """Extract the bucket name and image file from a GCS URL."""
        match = re.match(r"gs://([^/]+)/(.+)", gcs_url)
        if match:
            return match.group(1), match.group(2)
        raise ValueError("Invalid GCS URL format")

    def _generate_image_name_from_gcs_url(self, gcs_url: str) -> str:
        """Generate a unique image name based on the GCS URL."""
        _, image_file = self._parse_gcs_url(gcs_url)
        return re.sub(r"[^a-zA-Z0-9-]", "-", image_file)

    def _load_custom_image(
        self, project_id: str, image_name: str, bucket_name: str, image_file: str
    ):
        """Load a custom RAW image into GCE."""
        images_client = compute_v1.ImagesClient()
        image = compute_v1.Image()
        image.name = image_name
        image.raw_disk = compute_v1.RawDisk(source=f"gs://{bucket_name}/{image_file}")

        operation = images_client.insert(project=project_id, image_resource=image)
        operation.result()

    def _parse_machine_type(self, machine_type: str) -> (int, str):
        """Parse the machine type to extract CPU and memory info.

        Args:
            machine_type (str): The machine type string (e.g., 'zones/us-central1-a/machineTypes/n1-standard-1').

        Returns:
            (int, str): CPU cores and memory in GB.
        """
        parts = machine_type.split("/")
        if len(parts) >= 4 and parts[3].startswith("custom-"):
            cpu_memory = parts[3].split("-")
            return int(cpu_memory[1]), f"{int(cpu_memory[2]) // 1024}gb"
        return 0, "unknown"

    def delete(self, name: str) -> None:
        instance_client = compute_v1.InstancesClient()
        operation = instance_client.delete(
            project=self.project_id, zone=self.zone, instance=name
        )
        operation.result()  # Wait for operation to complete

        # Delete the Desktop record
        Desktop.delete(name)

    def start(self, name: str) -> None:
        instance_client = compute_v1.InstancesClient()
        operation = instance_client.start(
            project=self.project_id, zone=self.zone, instance=name
        )
        operation.result()  # Wait for the operation to complete

    def stop(self, name: str) -> None:
        instance_client = compute_v1.InstancesClient()
        operation = instance_client.stop(
            project=self.project_id, zone=self.zone, instance=name
        )
        operation.result()  # Wait for the operation to complete

    def list(self) -> List[Desktop]:
        desktops = Desktop.list()
        out = []
        for desktop in desktops:
            if desktop.provider.type == "gce":
                out.append(desktop)

        return out

    def get(self, name: str) -> Desktop:
        try:
            return Desktop.load(name)
        except ValueError:
            return None

    def to_data(self) -> V1ProviderData:
        """Convert to a ProviderData object

        Returns:
            ProviderData: ProviderData object
        """
        return V1ProviderData(
            type="gcpe", args={"project_id": self.project_id, "zone": self.zone}
        )

    @classmethod
    def from_data(cls, data: V1ProviderData) -> GCEProvider:
        """From provider data

        Args:
            data (ProviderData): Provider data
        """
        out = cls.__new__(GCEProvider)
        out.project_id = data.args["project_id"]
        out.zone = data.args["zone"]
        return out


def create_custom_image(project_id, image_name, bucket_name, image_file):
    """
    Create a custom image from a file in Cloud Storage.

    Args:
    project_id (str): The ID of the Google Cloud project.
    image_name (str): The name to assign to the new custom image.
    bucket_name (str): The name of the Google Cloud Storage bucket where the image file is stored.
    image_file (str): The name of the image file in the Google Cloud Storage bucket.

    Returns:
    The operation result of creating the image.
    """
    images_client = compute_v1.ImagesClient()
    image = compute_v1.Image()
    image.name = image_name
    image.source_image = f"gs://{bucket_name}/{image_file}"

    operation = images_client.insert(project=project_id, image_resource=image)
    return operation.result()


def create_vm_instance(project_id, zone, instance_name, machine_type, image_name):
    """
    Create a new VM instance with the specified custom image.

    Args:
    project_id (str): The ID of the Google Cloud project.
    zone (str): The zone where the VM instance will be created.
    instance_name (str): The name of the new VM instance.
    machine_type (str): The machine type for the new VM instance (e.g., 'n1-standard-1').
    image_name (str): The name of the custom image to use for the VM instance.

    Returns:
    The operation result of creating the VM instance.
    """
    instance_client = compute_v1.InstancesClient()
    instance = compute_v1.Instance()
    instance.name = instance_name
    instance.machine_type = f"zones/{zone}/machineTypes/{machine_type}"

    disk = compute_v1.AttachedDisk()
    disk.initialize_params = compute_v1.AttachedDiskInitializeParams()
    disk.initialize_params.source_image = image_name
    disk.auto_delete = True
    disk.boot = True
    instance.disks = [disk]

    network_interface = compute_v1.NetworkInterface()
    network_interface.name = "global/networks/default"  # Use appropriate VPC
    instance.network_interfaces = [network_interface]

    operation = instance_client.insert(
        project=project_id, zone=zone, instance_resource=instance
    )
    return operation.result()


def stop_instance(project_id, zone, instance_name):
    """
    Stops a Google Compute Engine instance.

    Args:
    project_id (str): The ID of the Google Cloud project.
    zone (str): The zone of the instance.
    instance_name (str): The name of the instance to stop.

    Returns:
    The operation result of stopping the instance.
    """
    instance_client = compute_v1.InstancesClient()

    operation = instance_client.stop(
        project=project_id, zone=zone, instance=instance_name
    )

    return operation.result()


def assign_external_ip(project_id, zone, instance_name):
    """
    Assigns an ephemeral external IP to a Google Compute Engine instance.

    Args:
    project_id (str): The ID of the Google Cloud project.
    zone (str): The zone of the instance.
    instance_name (str): The name of the instance to which the external IP will be assigned.

    Returns:
    The operation result of updating the instance's network interface.
    """
    instance_client = compute_v1.InstancesClient()

    # Retrieve the instance
    instance = instance_client.get(
        project=project_id, zone=zone, instance=instance_name
    )

    # Find the network interface
    for network_interface in instance.network_interfaces:
        if not network_interface.access_configs:
            # Add an access config (external IP) to the instance
            access_config = compute_v1.AccessConfig(nat_ip="", network_tier="PREMIUM")
            network_interface.access_configs = [access_config]
            break

    # Perform the update
    operation = instance_client.update(instance=instance, project=project_id, zone=zone)
    return operation.result()


def create_firewall_rule(project_id, rule_name, network, ports):
    """
    Creates a firewall rule to allow incoming traffic on specified ports.

    Args:
    project_id (str): The ID of the Google Cloud project.
    rule_name (str): The name of the firewall rule.
    network (str): The network where the rule will be applied.
    ports (list of str): A list of port numbers to allow (e.g., ['80', '443'] for HTTP and HTTPS).

    Returns:
    The operation result of inserting the firewall rule.
    """
    firewall_client = compute_v1.FirewallsClient()
    firewall = compute_v1.Firewall()
    firewall.name = rule_name
    firewall.direction = compute_v1.Firewall.Direction.INGRESS
    firewall.allowed = [{"IPProtocol": "tcp", "ports": ports}]
    firewall.network = network

    operation = firewall_client.insert(project=project_id, firewall_resource=firewall)
    return operation.result()


# project_id = 'your-project-id'
# rule_name = 'allow-http-https'
# network = 'global/networks/default'
# ports = ['80', '443']

# create_firewall_rule(project_id, rule_name, network, ports)
