from __future__ import annotations
import subprocess
import psutil
from typing import List, Optional
import os
from urllib.parse import urlparse
import tempfile
import time

import pycdlib
import requests
import docker
from namesgenerator import get_random_name

from .base import DesktopVM, DesktopProvider
from .img import JAMMY
from agentdesk.server.models import V1ProviderData
from agentdesk.util import (
    check_command_availability,
    find_ssh_public_key,
    get_docker_host,
)
from agentdesk.proxy import ensure_ssh_proxy

META_PYTHON_IMAGE = "python:3.9-slim"
META_CONTAINER_NAME = "http_server"


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
            image = os.path.expanduser(image)
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
      - { ssh_key }
    sudo: ALL=(ALL) NOPASSWD:ALL
    groups: sudo
    shell: /bin/bash
"""
        meta_data = f"""instance-id: {name}
local-hostname: {name}
"""
        sockify_port: int = 6080
        agentd_port: int = 8000
        # meta_data_port = 8123
        ssh_port = 2222

        self._create_iso("cidata.iso", user_data, meta_data)

        # cloud_init_dir = os.path.expanduser(f"~/.agentsea/cloud_init")
        # os.makedirs(cloud_init_dir, exist_ok=True)

        # self._create_cloud_init_dir(cloud_init_dir, user_data, meta_data)
        # self._run_cloud_metadata_server(
        #     directory=cloud_init_dir, host_port=meta_data_port
        # )

        ssh_user = "agentsea"
        ssh_host = "localhost"

        # ensure_ssh_proxy(6080, ssh_user, ssh_host)

        # QEMU command
        # command = (
        #     f"qemu-system-x86_64 -nographic -hda {image} -m {memory}G "
        #     f"-smp {cpu} -netdev user,id=vmnet,hostfwd=tcp::{sockify_port}-:6080,hostfwd=tcp::{agentd_port}-:8000,hostfwd=tcp::{ssh_port}-:22 "
        #     "-device e1000,netdev=vmnet "
        #     f"-smbios type=1,serial=ds='nocloud;s=http://10.0.2.2:{meta_data_port}/'"
        # )
        command = (
            f"qemu-system-x86_64 -nographic -hda {image_path} -m {memory}G "
            f"-smp {cpu} -netdev user,id=vmnet,hostfwd=tcp::{sockify_port}-:6080,hostfwd=tcp::{agentd_port}-:8000,hostfwd=tcp::{ssh_port}-:22 "
            "-device e1000,netdev=vmnet "
            f"-cdrom cidata.iso"
        )
        # "-drive file=user-data.iso,media=cdrom"
        # f"-drive file='user-data.iso',format=raw,if=virtio"

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
            metadata={},
        )
        return desktop

    # def _create_cloud_init_dir(
    #     self, cloud_init_dir: str, user_data: str, meta_data: str
    # ) -> None:
    #     # Directory to store cloud-init files

    #     meta_data_path = os.path.join(cloud_init_dir, "meta-data")
    #     with open(meta_data_path, "w") as f:
    #         f.write(meta_data)

    #     user_data_path = os.path.join(cloud_init_dir, "user-data")
    #     with open(user_data_path, "w") as f:
    #         f.write(user_data)

    #     # create an empty vendor-data file
    #     vendor_data_path = os.path.join(cloud_init_dir, "vendor-data")
    #     with open(vendor_data_path, "w") as f:
    #         f.write("")
    #     return

    # def _run_cloud_metadata_server(
    #     self, directory: str = ".", host_port: int = 8123
    # ) -> None:
    #     # https://cloudinit.readthedocs.io/en/latest/tutorial/qemu.html

    #     host = get_docker_host()
    #     os.environ["DOCKER_HOST"] = host
    #     client = docker.from_env()

    #     try:
    #         container = client.containers.get(META_CONTAINER_NAME)
    #         if container.status != "running":
    #             print(
    #                 f"Container '{META_CONTAINER_NAME}' exists but is not running. Starting it..."
    #             )
    #             container.start()
    #         else:
    #             print(f"Container '{META_CONTAINER_NAME}' is already running.")
    #     except docker.errors.NotFound:
    #         print(
    #             "Metadata container does not exist. Creating and starting a new one..."
    #         )
    #         container = client.containers.run(
    #             META_PYTHON_IMAGE,
    #             command=f"python -m http.server --directory /app 8000",
    #             ports={"8000/tcp": str(host_port)},
    #             volumes={os.path.abspath(directory): {"bind": "/app", "mode": "rw"}},
    #             detach=True,
    #             name=META_CONTAINER_NAME,
    #         )
    #         print(f"Waiting for container '{META_CONTAINER_NAME}' to start...")
    #         time.sleep(5)

    # def _create_cloud_init_iso(
    #     self, user_data: str, meta_data: str, output_iso_path: str = "user-data.iso"
    # ) -> None:
    #     # Create a new ISO
    #     iso = pycdlib.PyCdlib()
    #     iso.new(joliet=3, rock_ridge="1.09")

    #     # Use the tempfile module to create temporary files for user-data and meta-data
    #     with tempfile.NamedTemporaryFile(
    #         mode="w", delete=False
    #     ) as user_data_file, tempfile.NamedTemporaryFile(
    #         mode="w", delete=False
    #     ) as meta_data_file:
    #         user_data_file.write(user_data)
    #         meta_data_file.write(meta_data)

    #         user_data_path = user_data_file.name
    #         meta_data_path = meta_data_file.name

    #     # Add the user-data and meta-data files to the ISO
    #     iso.add_file(
    #         user_data_path,
    #         iso_path="/USERDATA.;1",
    #         rr_name="user-data",
    #         joliet_path="/user-data",
    #     )
    #     iso.add_file(
    #         meta_data_path,
    #         iso_path="/METADAT.;1",
    #         rr_name="meta-data",
    #         joliet_path="/meta-data",
    #     )

    #     # Write the ISO to a file and close it
    #     iso.write(output_iso_path)
    #     iso.close()

    #     # Clean up the temporary files
    #     os.remove(user_data_path)
    #     os.remove(meta_data_path)

    # def _create_cloud_init_iso(
    #     self, user_data: str, meta_data: str = "{}", iso_path: str = "user-data.iso"
    # ) -> None:
    #     """Create an ISO with cloud-init user-data using pycdlib"""

    #     iso = pycdlib.PyCdlib()
    #     iso.new(udf="2.60")
    #     # iso.new(interchange_level=3)

    #     user_data_filename = "/USERDATA.;1"
    #     meta_data_filename = "/METADATA.;1"

    #     # Add the cloud-init user-data
    #     iso.add_fp(
    #         io.BytesIO(user_data.encode("utf-8")),
    #         len(user_data),
    #         user_data_filename,
    #         udf_path="/user-data",
    #     )
    #     iso.add_fp(
    #         io.BytesIO(meta_data.encode("utf-8")),
    #         len(meta_data),
    #         meta_data_filename,
    #         udf_path="/meta-data",
    #     )
    #     iso.write(iso_path)
    #     iso.close()

    def _create_iso(self, output_iso: str, user_data: str, meta_data: str) -> None:
        iso = pycdlib.PyCdlib()
        iso.new(joliet=3, rock_ridge="1.09", vol_ident="cidata")

        # Use the tempfile module to create temporary files for user-data and meta-data
        with tempfile.NamedTemporaryFile(
            mode="w", delete=False
        ) as user_data_file, tempfile.NamedTemporaryFile(
            mode="w", delete=False
        ) as meta_data_file:
            user_data_file.write(user_data)
            meta_data_file.write(meta_data)

            user_data_path = user_data_file.name
            meta_data_path = meta_data_file.name

        # Add user-data and meta-data files
        iso.add_file(
            user_data_path,
            "/USERDATA.;1",
            joliet_path="/USERDATA.;1",
            rr_name="user-data",
        )
        iso.add_file(
            meta_data_path,
            "/METADATA.;1",
            joliet_path="/METADATA.;1",
            rr_name="meta-data",
        )

        # Write to an ISO file
        iso.write(output_iso)
        iso.close()

        # Clean up the temporary files
        os.remove(user_data_path)
        os.remove(meta_data_path)

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
