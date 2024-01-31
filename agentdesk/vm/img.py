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
    "Ubuntu 22.04 Jammy server",
    gce="gs://agentsea-vms/ubuntu_jammy.tar.gz",
    ec2=None,
    qcow2="https://storage.googleapis.com/agentsea-vms/ubuntu_jammy.raw",
)
