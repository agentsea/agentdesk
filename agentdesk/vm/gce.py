from __future__ import annotations
from typing import List, Optional, Dict
import re
import time
import atexit

from google.cloud import compute_v1
from google.cloud import _helpers
from namesgenerator import get_random_name
import requests

from .base import DesktopVM, DesktopProvider
from .img import JAMMY
from agentdesk.server.models import V1ProviderData
from agentdesk.util import find_ssh_public_key, find_open_port
from agentdesk.proxy import ensure_ssh_proxy, cleanup_proxy


class GCEProvider(DesktopProvider):
    """A VM provider using GCE"""

    def __init__(
        self,
        project_id: Optional[str] = None,
        zone: str = "us-central1-a",
        region: Optional[str] = "us-central1",
    ):
        """Initialize the GCP VM Provider with project and zone details."""
        self.project_id = project_id or _helpers._determine_default_project()
        self.zone = zone
        self.region = region

        # print("using project id: ", self.project_id)

    def create(
        self,
        name: Optional[str] = None,
        image: Optional[str] = None,
        memory: int = 4,
        cpu: int = 2,
        disk: str = "30gb",
        tags: Optional[Dict[str, str]] = None,
        reserve_ip: bool = False,
        ssh_key: Optional[str] = None,
    ) -> DesktopVM:
        """Create a VM in GCP."""

        if not name:
            name = get_random_name(sep="-")

        if DesktopVM.name_exists(name):
            raise ValueError(f"VM name '{name}' already exists")

        if not image:
            image = JAMMY.gce

        # bucket_name, image_file = self._parse_gcs_url(image)
        # image_name = self._generate_image_name_from_gcs_url(image)

        images_client = compute_v1.ImagesClient()

        # Check if the image exists
        img = images_client.get(project=self.project_id, image=image)
        if img.status != "READY":
            raise ValueError("Image is not ready")

        instance_client = compute_v1.InstancesClient()
        machine_type = f"zones/{self.zone}/machineTypes/custom-{cpu}-{memory*1024}"
        image_project_id = "agentsea-dev"
        source_image_url = f"projects/{image_project_id}/global/images/{image}"

        disk_config = compute_v1.AttachedDiskInitializeParams(
            disk_size_gb=int(disk[:-2]), source_image=source_image_url
        )
        _disk = compute_v1.AttachedDisk(
            boot=True, auto_delete=True, initialize_params=disk_config
        )
        access_configs = [compute_v1.AccessConfig(name="External NAT")]
        network_interface = compute_v1.NetworkInterface(
            name="global/networks/default",
            access_configs=access_configs,
        )

        # Network tags for firewall rules (SSH-only access)
        network_tags = ["ssh-only"]

        if not tags:
            tags = {}
        tags["provisioner"] = "agentdesk"  # Your custom labels

        if not ssh_key:
            ssh_key = find_ssh_public_key()

        if ssh_key:
            metadata = compute_v1.Metadata(
                items=[{"key": "ssh-keys", "value": f"agentsea:{ssh_key}"}]
            )
        else:
            raise ValueError("No SSH key provided and could not find one")

        # Instance creation with network tags and metadata
        instance = compute_v1.Instance(
            name=name,
            machine_type=machine_type,
            disks=[_disk],
            network_interfaces=[network_interface],
            tags=compute_v1.Tags(items=network_tags),
            labels=tags,
            metadata=metadata,
        )

        if reserve_ip:
            static_ip_name = f"{name}-ip"
            reserved_ip = self.reserve_static_ip(static_ip_name)
            access_config = compute_v1.AccessConfig(
                nat_ip=reserved_ip, name="External NAT"
            )
            instance.network_interfaces[0].access_configs = [access_config]

        operation = instance_client.insert(
            project=self.project_id, zone=self.zone, instance_resource=instance
        )
        operation.result()

        created_instance = instance_client.get(
            project=self.project_id, zone=self.zone, instance=name
        )
        ip_address = created_instance.network_interfaces[0].access_configs[0].nat_i_p

        # Wait for the VM to be ready
        self._wait_till_ready(ip_address)

        new_desktop = DesktopVM(
            name=name,
            id=str(created_instance.id),
            addr=ip_address,
            cpu=cpu,
            memory=memory,
            disk=disk,
            image=image,
            provider=self.to_data(),
            requires_proxy=True,
        )
        print(f"\nsuccessfully created desktop '{name}'")
        return new_desktop

    def _wait_till_ready(
        self, addr: str, local_agentd_port: Optional[int] = None
    ) -> None:
        print("waiting for desktop to be ready...")
        if not local_agentd_port:
            local_agentd_port = find_open_port(8000, 9000)

        ready = False
        while not ready:
            print("waiting for desktop to be ready...")
            time.sleep(3)
            try:
                print("ensuring up ssh proxy...")
                pid = ensure_ssh_proxy(
                    local_port=local_agentd_port, remote_port=8000, ssh_host=addr
                )
                atexit.register(cleanup_proxy, pid)

                print("calling agentd...")
                response = requests.get(f"http://localhost:{local_agentd_port}/health")
                print("agentd response: ", response)
                if response.status_code == 200:
                    ready = True

                cleanup_proxy(pid)
                atexit.unregister(cleanup_proxy)
            except:
                cleanup_proxy(pid)
                pass

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
        self,
        image_name: str,
        image_uri: str,
    ):
        """Load a custom RAW image into GCE."""
        # NOTE: this may require enabling specific privileges to the cloud build service account
        # command = [
        #     "gcloud",
        #     "compute",
        #     "images",
        #     "create",
        #     image_name,
        #     "--project=" + self.project_id,
        #     "--source-uri=" + image_uri,
        # ]

        # subprocess.run(command, check=True)
        client = compute_v1.ImagesClient()

        image_resource = {
            "name": "ubuntu-jammy-agentd-test",
            "rawDisk": {"source": "gs://agentsea-vms/ubuntu_jammy.raw"},
            "family": "ubuntu-jammy-agentd",
            "guestOsFeatures": [{"type": "VIRTIO_SCSI_MULTIQUEUE"}],
            "licenses": [
                "projects/compute-image-tools/global/licenses/debian-10-buster"
            ],
        }

        request = compute_v1.InsertImageRequest(
            project=self.project_id, zone=self.zone, imageResource=image_resource
        )

        response = client.insert(request=request)
        print(response)

        images_client = compute_v1.ImagesClient()

        ready = False
        while not ready:
            print("checking if image is ready...")
            img = images_client.get(project=self.project_id, image=image_name)
            print("found img: ", img)
            print("img status: ", img.status)
            # FAILED, PENDING, or READY
            if img.status == "READY":
                print("image ready")
                break
            elif img.status == "FAILED":
                raise ValueError("Could not import VM image into GCE")
            elif img.status == "PENDING":
                print("image pending")
                time.sleep(10)
            else:
                raise ValueError("Unknown image status")

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
        desktop = DesktopVM.find(name)
        if not desktop:
            raise ValueError(f"Desktop {name} not found")

        instance_client = compute_v1.InstancesClient()
        operation = instance_client.delete(
            project=self.project_id,
            zone=self.zone,
            instance=name,
        )
        operation.result()  # Wait for operation to complete

        # Delete the Desktop record
        desktop.remove()

    def start(self, name: str) -> None:
        desk = DesktopVM.find(name)
        if not desk:
            raise ValueError(f"Desktop {name} not found")
        instance_client = compute_v1.InstancesClient()
        operation = instance_client.start(
            project=self.project_id,
            zone=self.zone,
            instance=name,
        )
        operation.result()  # Wait for the operation to complete
        created_instance = instance_client.get(
            project=self.project_id, zone=self.zone, instance=name
        )
        ip_address = created_instance.network_interfaces[0].access_configs[0].nat_i_p
        desk.addr = ip_address

        self._wait_till_ready(ip_address)
        desk.status = "running"
        desk.save()

    def stop(self, name: str) -> None:
        desk = DesktopVM.find(name)
        if not desk:
            raise ValueError(f"Desktop {name} not found")
        instance_client = compute_v1.InstancesClient()
        operation = instance_client.stop(
            project=self.project_id,
            zone=self.zone,
            instance=name,
        )
        operation.result()  # Wait for the operation to complete
        desk.status = "stopped"
        desk.save()

    def list(self) -> List[DesktopVM]:
        desktops = DesktopVM.list()
        out = []
        for desktop in desktops:
            if desktop.provider.type == "gce":
                out.append(desktop)

        return out

    def get(self, name: str) -> Optional[DesktopVM]:
        try:
            return DesktopVM.find(name)
        except ValueError:
            return None

    def to_data(self) -> V1ProviderData:
        """Convert to a ProviderData object

        Returns:
            ProviderData: ProviderData object
        """
        args = {}
        if self.project_id:
            args["project_id"] = self.project_id
        if self.zone:
            args["zone"] = self.zone

        data = V1ProviderData(type="gce", args=args)
        return data

    @classmethod
    def from_data(cls, data: V1ProviderData) -> GCEProvider:
        """From provider data

        Args:
            data (ProviderData): Provider data
        """

        if data.args:
            return GCEProvider(**data.args)

        return GCEProvider()

    def refresh(self, log: bool = True) -> None:
        """Refresh the state of all VMs managed by this GCEProvider."""
        instance_client = compute_v1.InstancesClient()

        # List all instances in the project and zone
        request = compute_v1.ListInstancesRequest(
            project=self.project_id,
            zone=self.zone,
        )
        response = instance_client.list(request=request)
        # Build a list of all GCE instance names for comparison
        gce_instance_names = [instance.name for instance in response]

        # Iterate over all DesktopVM instances managed by this provider
        for vm in DesktopVM.list():
            if vm.provider.type != "gce":
                continue

            # Check if the VM still exists in GCE
            if vm.name not in gce_instance_names:
                # VM no longer exists in GCE, so remove it
                if log:
                    print(f"removing vm '{vm.name}' from state")
                vm.remove()
                return
            else:
                # VM exists, update its details
                instance = instance_client.get(
                    project=self.project_id,
                    zone=self.zone,
                    instance=vm.name,
                )
                # Assuming the first network interface and access config is used for the public IP
                remote_addr = instance.network_interfaces[0].access_configs[0].nat_i_p
                remote_status = "running" if instance.status == "RUNNING" else "stopped"

                if remote_status != vm.status or remote_addr != vm.addr:
                    if log:
                        print(f"updating vm '{vm.name}' state")
                    vm.status = remote_status
                    vm.addr = remote_addr
                    vm.save()
                return


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
    print("\n!image name: ", image_name)
    images_client = compute_v1.ImagesClient()
    image = compute_v1.Image()
    image.name = image_name
    image.source_image = f"gs://{bucket_name}/{image_file}"

    print("image: ", image)
    print("project: ", project_id)

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
