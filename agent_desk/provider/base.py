from abc import ABC, abstractmethod
from typing import List


class VM:
    """A virtual machine."""

    id: str
    provider: str
    vm_name: str
    vm_image: str
    memory: str
    cpu: int
    disk: str
    ip: str


class VMProvider(ABC):
    """A provider of vms"""

    @abstractmethod
    def create(self, name: str, image: str, memory: str, cpu: int, disk: str) -> VM:
        pass

    @abstractmethod
    def delete(self, name: str) -> None:
        pass

    @abstractmethod
    def start(self, name: str) -> None:
        pass

    @abstractmethod
    def stop(self, name: str) -> None:
        pass

    @abstractmethod
    def list(self) -> List[VM]:
        pass

    @abstractmethod
    def get(self, name: str) -> None:
        pass
