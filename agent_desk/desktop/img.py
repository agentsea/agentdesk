from dataclasses import dataclass
from typing import Optional


@dataclass
class Image:
    """A desktop vm image"""

    name: str
    description: str
    gcp: Optional[str] = None
    aws: Optional[str] = None
    qcow2: Optional[str] = None


JAMMY = Image("jammy", "Ubuntu 22.04 Jammy server", "", "", "")
