from __future__ import annotations
from typing import List, Optional, Dict, Tuple, Any
import re
import time
import atexit

from google.cloud import compute_v1
from google.cloud import _helpers
from google.oauth2.service_account import Credentials
from namesgenerator import get_random_name
import requests
import json

from .base import DesktopVM, DesktopProvider
from .img import JAMMY
from agentdesk.server.models import V1ProviderData
from agentdesk.util import find_ssh_public_key, find_open_port
from agentdesk.proxy import ensure_ssh_proxy, cleanup_proxy


class GCEProvider(DesktopProvider):
    """VM provider using GCP Compute Engine"""

    def __init__(
        self,
        project_id: Optional[str] = None,
        zone: str = "us-central1-a",
        region: Optional[str] = "us-central1",
        gcp_credentials_json: Optional[str] = None,
    ):
        """Initialize the GCP VM Provider with project, zone, region, and optional JSON credentials."""
        self.project_id = project_id or _helpers._determine_default_project()
        self.zone = zone
        self.region = region
        if gcp_credentials_json:
            credentials_info = json.loads(gcp_credentials_json)
            self.credentials = Credentials.from_service_account_info(credentials_info)
        else:
            self.credentials = None
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
        owner_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
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

        images_client = compute_v1.ImagesClient(credentials=self.credentials)

        # Check if the image exists
        img = images_client.get(project=self.project_id, image=image)
        if img.status != "READY":
            raise ValueError("Image is not ready")

        instance_client = compute_v1.InstancesClient(credentials=self.credentials)
        machine_type = f"zones/{self.zone}/machineTypes/custom-{cpu}-{memory * 1024}"
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
            owner_id=owner_id,
            metadata=metadata,
            ssh_key=ssh_key,
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
            except Exception as e:
                print("Exception while waiting for desktop to be ready: ", e)
                try:
                    cleanup_proxy(pid)
                    atexit.unregister(cleanup_proxy)
                except:
                    pass

    def reserve_static_ip(self, name: str) -> str:
        """Reserve a static external IP address."""
        addresses_client = compute_v1.AddressesClient(credentials=self.credentials)
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
        firewall_client = compute_v1.FirewallsClient(credentials=self.credentials)
        firewall = compute_v1.Firewall()
        firewall.name = rule_name
        firewall.direction = compute_v1.Firewall.Direction.INGRESS
        firewall.allowed = [{"IPProtocol": "tcp", "ports": ports}]
        firewall.network = network

        operation = firewall_client.insert(
            project=self.project_id, firewall_resource=firewall
        )
        return operation.result()

    def _parse_gcs_url(self, gcs_url: str) -> Tuple[str, str]:
        """Extract the bucket name and image file from a GCS URL."""
        match = re.match(r"gs://([^/]+)/(.+)", gcs_url)
        if match:
            return match.group(1), match.group(2)
        raise ValueError("Invalid GCS URL format")

    def _generate_image_name_from_gcs_url(self, gcs_url: str) -> str:
        """Generate a unique image name based on the GCS URL."""
        _, image_file = self._parse_gcs_url(gcs_url)
        return re.sub(r"[^a-zA-Z0-9-]", "-", image_file)

    def _parse_machine_type(self, machine_type: str) -> Tuple[int, str]:
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
        desktop = DesktopVM.get(name)
        if not desktop:
            raise ValueError(f"Desktop {name} not found")

        instance_client = compute_v1.InstancesClient(credentials=self.credentials)
        operation = instance_client.delete(
            project=self.project_id,
            zone=self.zone,
            instance=name,
        )
        operation.result()  # Wait for operation to complete

        # Delete the Desktop record
        desktop.remove()

    def start(self, name: str) -> None:
        desk = DesktopVM.get(name)
        if not desk:
            raise ValueError(f"Desktop {name} not found")
        instance_client = compute_v1.InstancesClient(credentials=self.credentials)
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
        desk = DesktopVM.get(name)
        if not desk:
            raise ValueError(f"Desktop {name} not found")
        instance_client = compute_v1.InstancesClient(credentials=self.credentials)
        operation = instance_client.stop(
            project=self.project_id,
            zone=self.zone,
            instance=name,
        )
        operation.result()  # Wait for the operation to complete
        desk.status = "stopped"
        desk.save()

    def list(self) -> List[DesktopVM]:
        desktops = DesktopVM.find()
        out = []
        for desktop in desktops:
            if desktop.provider.type == "gce":
                out.append(desktop)

        return out

    def get(self, name: str) -> Optional[DesktopVM]:
        try:
            return DesktopVM.get(name)
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
        instance_client = compute_v1.InstancesClient(credentials=self.credentials)

        # List all instances in the project and zone
        request = compute_v1.ListInstancesRequest(
            project=self.project_id,
            zone=self.zone,
        )
        response = instance_client.list(request=request)
        # Build a list of all GCE instance names for comparison
        gce_instance_names = [instance.name for instance in response]

        # Iterate over all DesktopVM instances managed by this provider
        for vm in DesktopVM.find():
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
