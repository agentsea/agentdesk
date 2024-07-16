from dataclasses import dataclass
from typing import Optional


@dataclass
class Image:
    """A desktop vm image"""

    name: str
    description: str
    gce: Optional[str] = None
    ec2: Optional[str] = None
    qcow2: Optional[str] = None


JAMMY = Image(
    "jammy",
    "Ubuntu 22.04 Jammy server with agentd",
    gce="agentd-ubuntu-22-04-u20240711093315-patch1",
    ec2="agentd-ubuntu-22.04-20240529073401-patch1",
    qcow2="https://storage.googleapis.com/agentsea-vms/jammy/agentd-ubuntu-qemu-07202412121737-patch1.qcow2",
)
