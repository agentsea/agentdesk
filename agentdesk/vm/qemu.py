from __future__ import annotations
import subprocess
import psutil
from typing import List, Optional, Dict, Any
import os
from urllib.parse import urlparse
import tempfile
import time
import signal
import logging
import shutil

import pycdlib
import requests
from namesgenerator import get_random_name
from tqdm import tqdm
from agentdesk.key import SSHKeyPair

from .base import DesktopVM, DesktopProvider
from .img import JAMMY
from agentdesk.server.models import V1ProviderData
from agentdesk.config import AGENTSEA_HOME
from agentdesk.util import (
    check_command_availability,
    generate_short_hash,
    generate_random_string,
)

META_PYTHON_IMAGE = "python:3.9-slim"
META_CONTAINER_NAME = "http_server"

logger = logging.getLogger(__name__)


class QemuProvider(DesktopProvider):
    """A VM provider using local QEMU virtual machines."""

    def __init__(self, log_vm: bool = False) -> None:
        self.log_vm = log_vm

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
    ) -> DesktopVM:
        """Create a local QEMU VM locally"""

        if not check_command_availability("qemu-system-x86_64"):
            raise EnvironmentError(
                "qemu-system-x86_64 is not installed. Please install QEMU."
            )

        if not name:
            name = get_random_name(sep="-")
            if not name:
                raise ValueError("could not generate name")

        if DesktopVM.name_exists(name):  # type: ignore
            raise ValueError(f"VM name '{name}' already exists")

        # Directory to store VM images
        vm_dir = os.path.join(AGENTSEA_HOME, "vms")
        os.makedirs(vm_dir, exist_ok=True)

        if not image:
            image = JAMMY.qcow2
            image_name = JAMMY.name
        elif image.startswith("https://"):
            parsed_url = urlparse(image)
            image_name = parsed_url.hostname + parsed_url.path.replace(  # type: ignore
                "/", "_"
            )
        else:
            image = os.path.expanduser(image)
            if not os.path.exists(image):
                raise FileNotFoundError(
                    f"The specified image path '{image}' does not exist."
                )
            image_name = os.path.basename(image)

        base_image_path = os.path.join(vm_dir, image_name)

        # Download image only if it does not exist
        if not os.path.exists(base_image_path) and image.startswith("https://"):  # type: ignore
            print(f"Downloading image '{image}'...")
            response = requests.get(image, stream=True)  # type: ignore
            total_size_in_bytes = int(response.headers.get("content-length", 0))
            block_size = 8192  # Size of each chunk

            progress_bar = tqdm(total=total_size_in_bytes, unit="iB", unit_scale=True)
            with open(base_image_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=block_size):
                    progress_bar.update(len(chunk))
                    f.write(chunk)
            progress_bar.close()

        image_path = os.path.join(vm_dir, f"{name}.qcow2")
        shutil.copy(base_image_path, image_path)

        # Find or generate an SSH key if not provided
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

        # Generate user-data
        user_data = f"""#cloud-config
users:
  - name: agentsea
    ssh_authorized_keys:
      - {public_ssh_key}
    sudo: ALL=(ALL) NOPASSWD:ALL
    groups: sudo
    shell: /bin/bash
"""
        meta_data = f"""instance-id: {name}
local-hostname: {name}
"""
        sockify_port: int = 6080
        agentd_port: int = 8000
        ssh_port = 2222

        self._create_iso("cidata.iso", user_data, meta_data)

        pid_file = f"{name}.pid"  # File to store the PID

        command = (
            f"nohup qemu-system-x86_64 -nographic -hda {image_path} -m {memory}G "
            f"-smp {cpu} -netdev user,id=vmnet,hostfwd=tcp::5900-:5900,hostfwd=tcp::{sockify_port}-:6080,hostfwd=tcp::{agentd_port}-:8000,hostfwd=tcp::{ssh_port}-:22 "
            "-device e1000,netdev=vmnet "
            f"-cdrom cidata.iso >/dev/null 2>&1 & echo $! > {pid_file}"
        )

        # Set environment variables
        env = os.environ.copy()
        env["AGENTDESK"] = name

        # Start the QEMU process
        try:
            if self.log_vm:
                process = subprocess.Popen(command, shell=True)
                pid = process.pid
            else:
                subprocess.run(command, shell=True, env=env, check=True)
                self._wait_till_ready(agentd_port)

                # Read the PID from the file
                with open(pid_file, "r") as file:
                    pid = int(file.read().strip())

                os.remove(pid_file)

            self._wait_till_ready(agentd_port)

        except subprocess.CalledProcessError as e:
            print(f"Command '{command}' returned non-zero exit status {e.returncode}.")
            raise
        except KeyboardInterrupt:
            print("Keyboard interrupt received, terminating process...")
            os.killpg(os.getpgid(process.pid), signal.SIGINT)  # type: ignore
            raise
        except Exception as e:
            print(f"An error occurred: {e}")
            os.killpg(os.getpgid(process.pid), signal.SIGINT)  # type: ignore
            raise

        print("connected to desktop")

        # Create and return a Desktop object
        desktop = DesktopVM(
            name=name,  # type: ignore
            addr="localhost",
            cpu=cpu,
            memory=memory,  # type: ignore
            disk=disk,
            pid=pid,
            image=image,
            provider=self.to_data(),
            requires_proxy=False,
            ssh_port=ssh_port,
            owner_id=owner_id,
            metadata=metadata,
            key_pair_name=key_pair.name,
        )
        print(f"\nsuccessfully created desktop '{name}'")
        return desktop

    def _wait_till_ready(self, agentd_port: int) -> None:
        ready = False
        while not ready:
            print("waiting for desktop to be ready...")
            time.sleep(3)
            try:
                print("calling agentd...")
                response = requests.get(f"http://localhost:{agentd_port}/health")
                print("agentd response: ", response)
                if response.status_code == 200:
                    ready = True
            except Exception:
                pass

    def _create_iso(self, output_iso: str, user_data: str, meta_data: str) -> None:
        iso = pycdlib.PyCdlib()  # type: ignore
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

    def delete(self, name: str, owner_id: Optional[str] = None) -> None:
        """Delete a local QEMU VM."""
        desktop = DesktopVM.get(name, owner_id=owner_id)
        if not desktop:
            raise ValueError(f"Desktop '{name}' does not exist.")

        # Find the process by AGENTDESK environment variable
        found_process = False
        for process in psutil.process_iter(["pid", "environ"]):
            try:
                env = process.environ()
                if "AGENTDESK" in env and env["AGENTDESK"] == name:
                    process.terminate()
                    process.wait()
                    found_process = True
                    break
            except (
                psutil.NoSuchProcess,
                psutil.AccessDenied,
                psutil.ZombieProcess,
            ) as e:
                logger.debug(f"Error accessing process: {e}")
                continue

        if not found_process:
            print(
                f"No running process found for VM '{name}' with AGENTDESK environment variable."
            )

        desktop.remove()
        logger.debug(f"Deleted desktop VM record for '{name}'.")

        vm_dir = os.path.join(AGENTSEA_HOME, "vms")
        image_path = os.path.join(vm_dir, f"{name}.qcow2")

        os.remove(image_path)

        keys = SSHKeyPair.find(owner_id=owner_id or "local")
        if keys:
            for key in keys:
                if (
                    "generated_for" in key.metadata
                    and key.metadata["generated_for"] == name
                ):
                    key.delete(key.name, key.owner_id)
                    logger.debug(f"Deleted SSH key {key.name}")

    def start(
        self,
        name: str,
        private_ssh_key: Optional[str] = None,
        owner_id: Optional[str] = None,
    ) -> None:
        """Start a local QEMU VM."""
        # Starting a local VM might be equivalent to creating it, as QEMU processes don't persist.
        raise NotImplementedError(
            "Start method is not available for QEMU VMs. Use create() instead."
        )

    def stop(self, name: str, owner_id: Optional[str] = None) -> None:
        """Stop a local QEMU VM."""
        self.delete(name, owner_id=owner_id)

    def list(self) -> List[DesktopVM]:
        """List local QEMU VMs."""
        desktops = DesktopVM.find()
        return [
            desktop
            for desktop in desktops
            if isinstance(desktop.provider, V1ProviderData)
            and desktop.provider.type == "qemu"
        ]

    def get(self, name: str, owner_id: Optional[str] = None) -> Optional[DesktopVM]:
        """Get a local QEMU VM."""
        try:
            desktop = DesktopVM.get(name, owner_id=owner_id)
            if not desktop:
                return None
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
        return V1ProviderData(type="qemu", args={"log_vm": self.log_vm})

    @classmethod
    def from_data(cls, data: V1ProviderData) -> QemuProvider:
        """Create a provider from ProviderData."""
        if data.args:
            return cls(**data.args)
        return cls()

    def refresh(self, log: bool = True) -> None:
        """Refresh the state of all local QEMU VMs."""
        desktops = DesktopVM.find()

        for desktop in desktops:
            if (
                isinstance(desktop.provider, V1ProviderData)
                and desktop.provider.type == "qemu"
            ):
                # Check if the process is still running by AGENTDESK environment variable
                process_exists = False
                for process in psutil.process_iter(["pid", "environ"]):
                    try:
                        env = process.environ()
                        if "AGENTDESK" in env and env["AGENTDESK"] == desktop.name:
                            process_exists = True
                            break
                    except (
                        psutil.NoSuchProcess,
                        psutil.AccessDenied,
                        psutil.ZombieProcess,
                    ):
                        continue

                if not process_exists:
                    if log:
                        print(f"removing vm '{desktop.name}' from state")
                    desktop.remove()
