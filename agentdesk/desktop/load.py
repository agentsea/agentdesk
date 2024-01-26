from .ec2 import EC2Provider
from .gce import GCEProvider
from .qemu import QemuProvider
from .base import DesktopProvider
from agentdesk.server.models import V1ProviderData


def load_provider(data: V1ProviderData) -> DesktopProvider:
    """Load a DesktopProvider from a dictionary."""
    if data.type == "ec2":
        return EC2Provider.from_data(data)
    elif data.type == "gce":
        return GCEProvider.from_data(data)
    elif data.type == "qemu":
        return QemuProvider.from_data(data)
    else:
        raise ValueError(f"Unknown provider type: {data.type}")
