from __future__ import annotations
import subprocess
import psutil
from typing import List, Optional

from .base import Desktop, DesktopProvider
from agentdesk.server.models import V1ProviderData


class QemuProvider(DesktopProvider):
    """A VM provider using local QEMU virtual machines."""

    def create(
        self,
        name: str,
        image: str,
        memory: str = "4gb",
        cpu: int = 2,
        disk: str = "30gb",
        sockify_port: int = 6080,
    ) -> Desktop:
        """Create a local QEMU VM."""
        command = f"qemu-system-x86_64 -hda {image} -m {memory} "
        command += f"-smp {cpu} -netdev user,id=vmnet,hostfwd=tcp::6080-:{sockify_port},hostfwd=tcp::8000-:8000 "
        command += "-device e1000,netdev=vmnet -vnc :0"

        process = subprocess.Popen(command, shell=True)

        # Create and return a Desktop object
        desktop = Desktop(
            name=name,
            addr="localhost",  # Address is localhost for QEMU VMs
            cpu=cpu,
            memory=memory,
            disk=disk,
            pid=process.pid,
            image=image,
            provider=self.to_data(),  # Create a V1ProviderData object representing this provider
        )
        return desktop

    def delete(self, name: str) -> None:
        """Delete a local QEMU VM."""
        desktop = Desktop.load(name)
        if psutil.pid_exists(desktop.pid):
            process = psutil.Process(desktop.pid)
            process.terminate()
            process.wait()
        Desktop.delete(desktop.id)

    def start(self, name: str) -> None:
        """Start a local QEMU VM."""
        # Starting a local VM might be equivalent to creating it, as QEMU processes don't persist.
        raise NotImplementedError(
            "Start method is not available for QEMU VMs. Use create() instead."
        )

    def stop(self, name: str) -> None:
        """Stop a local QEMU VM."""
        self.delete(name)

    def list(self) -> List[Desktop]:
        """List local QEMU VMs."""
        desktops = Desktop.list()
        return [
            desktop
            for desktop in desktops
            if isinstance(desktop.provider, V1ProviderData)
            and desktop.provider.type == "qemu"
        ]

    def get(self, name: str) -> Optional[Desktop]:
        """Get a local QEMU VM."""
        try:
            desktop = Desktop.load(name)
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
