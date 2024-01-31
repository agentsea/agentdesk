from __future__ import annotations
import subprocess
import psutil
from typing import List, Optional
import io
import os
from urllib.parse import urlparse

import pycdlib
import requests
from namesgenerator import get_random_name

from .base import DesktopVM, DesktopProvider
from .img import JAMMY
from agentdesk.server.models import V1ProviderData
from agentdesk.util import check_command_availability, find_ssh_public_key


class QemuProvider(DesktopProvider):
    """A VM provider using local QEMU virtual machines."""

    def create(
        self,
        name: Optional[str] = None,
        image: Optional[str] = None,
        memory: int = 4,
        cpu: int = 2,
        disk: str = "30gb",
        reserve_ip: bool = False,
        ssh_key: Optional[str] = None,
    ) -> DesktopVM:
        """Create a local QEMU VM locally"""

        if not check_command_availability("qemu-system-x86_64"):
            raise EnvironmentError(
                "qemu-system-x86_64 is not installed. Please install QEMU."
            )

        if not name:
            name = get_random_name()

        # Directory to store VM images
        vm_dir = os.path.expanduser(f"~/.agentsea/vms/{name}")
        os.makedirs(vm_dir, exist_ok=True)

        if not image:
            image = JAMMY.qcow2
            image_name = JAMMY.name
        elif image.startswith("https://"):
            parsed_url = urlparse(image)
            image_name = parsed_url.hostname + parsed_url.path.replace("/", "_")
        else:
            if not os.path.exists(image):
                raise FileNotFoundError(
                    f"The specified image path '{image}' does not exist."
                )
            image_name = os.path.basename(image)

        image_path = os.path.join(vm_dir, image_name)

        # Download image only if it does not exist
        if not os.path.exists(image_path) and image.startswith("https://"):
            response = requests.get(image, stream=True)
            with open(image_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)

        # Find or generate an SSH key if not provided
        ssh_key = ssh_key or find_ssh_public_key()
        if not ssh_key:
            raise ValueError("SSH key not provided or found")

        print("generating cloud config with ssh key: ", ssh_key)
        # Generate user-data
        user_data = f"""#cloud-config
users:
  - name: agentsea
    ssh_authorized_keys:
      - {ssh_key}

runcmd:
  - ufw allow 6080/tcp
  - ufw allow 8000/tcp
  - ufw enable
"""

        # Create an ISO with user-data for cloud-init
        self._create_cloud_init_iso(user_data)

        # QEMU command
        sockify_port: int = 6080
        agentd_port: int = 8000
        command = (
            f"qemu-system-x86_64 -nographic -hda {image} -m {memory}G "
            f"-smp {cpu} -netdev user,id=vmnet,hostfwd=tcp::{sockify_port}-:6080,hostfwd=tcp::{agentd_port}-:8000,hostfwd=tcp::2222-:22 "
            "-device e1000,netdev=vmnet "
            f"-drive file='user-data.iso',format=raw,if=virtio"
        )

        # Start the QEMU process
        process = subprocess.Popen(command, shell=True)

        # Create and return a Desktop object
        desktop = DesktopVM(
            name=name,
            addr="localhost",
            cpu=cpu,
            memory=memory,
            disk=disk,
            pid=process.pid,
            image=image,
            provider=self.to_data(),
        )
        return desktop

    def _create_cloud_init_iso(
        self, user_data: str, iso_path: str = "user-data.iso"
    ) -> None:
        """Create an ISO with cloud-init user-data using pycdlib."""
        iso = pycdlib.PyCdlib()
        iso.new(interchange_level=3)

        # ISO9660 filename in the 8.3 format: 8 characters for name, 3 for extension
        cloud_init_filename = "/user-data"

        # Add the cloud-init user-data
        iso.add_fp(
            io.BytesIO(user_data.encode("utf-8")), len(user_data), cloud_init_filename
        )
        iso.write(iso_path)
        iso.close()

    def delete(self, name: str) -> None:
        """Delete a local QEMU VM."""
        desktop = DesktopVM.load(name)
        if psutil.pid_exists(desktop.pid):
            process = psutil.Process(desktop.pid)
            process.terminate()
            process.wait()
        DesktopVM.delete(desktop.id)

    def start(self, name: str) -> None:
        """Start a local QEMU VM."""
        # Starting a local VM might be equivalent to creating it, as QEMU processes don't persist.
        raise NotImplementedError(
            "Start method is not available for QEMU VMs. Use create() instead."
        )

    def stop(self, name: str) -> None:
        """Stop a local QEMU VM."""
        self.delete(name)

    def list(self) -> List[DesktopVM]:
        """List local QEMU VMs."""
        desktops = DesktopVM.list()
        return [
            desktop
            for desktop in desktops
            if isinstance(desktop.provider, V1ProviderData)
            and desktop.provider.type == "qemu"
        ]

    def get(self, name: str) -> Optional[DesktopVM]:
        """Get a local QEMU VM."""
        try:
            desktop = DesktopVM.load(name)
            if (
                isinstance(desktop.provider, V1ProviderData)
                and desktop.provider.type == "qemu"
            ):
                return desktop
            return None
        except ValueError:
            return None

    def to_data(self) -> V1ProviderData:
        """Convert to a ProviderData object."""
        return V1ProviderData(type="qemu")

    @classmethod
    def from_data(cls, data: V1ProviderData) -> QemuProvider:
        """Create a provider from ProviderData."""
        return cls()
