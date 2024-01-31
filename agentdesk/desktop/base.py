from __future__ import annotations
from abc import ABC, abstractmethod
from typing import List, Optional, TypeVar, Any
import uuid
import time
import json
import webbrowser
import random
import os

import docker

from agentdesk.db.conn import WithDB
from agentdesk.db.models import V1DesktopRecord
from agentdesk.server.models import V1Desktop, V1Desktops, V1ProviderData
from agentdesk.util import get_docker_host, check_command_availability

UI_IMG = "us-central1-docker.pkg.dev/agentsea-dev/agentdesk/ui:a85fde68ac9849d9301be702f2092a8a299abe52"


class Desktop(WithDB):
    """A remote desktop which is accesible for AI agents"""

    def __init__(
        self,
        name: str,
        addr: str,
        cpu: Optional[int] = None,
        memory: Optional[str] = None,
        disk: Optional[str] = None,
        pid: Optional[int] = None,
        image: Optional[str] = None,
        provider: Optional[V1ProviderData] = None,
    ) -> None:
        self.name = name
        self.addr = addr
        self.cpu = cpu
        self.memory = memory
        self.disk = disk
        self.pid = pid
        self.id = str(uuid.uuid4())
        self.created = time.time()
        self.status = "active"
        self.image = image
        self.provider = provider

        self.save()

    def to_record(self) -> V1DesktopRecord:
        provider = None
        if self.provider:
            provider = json.dumps(self.provider.__dict__)
        return V1DesktopRecord(
            id=self.id,
            name=self.name,
            addr=self.addr,
            cpu=self.cpu,
            created=self.created,
            memory=self.memory,
            disk=self.disk,
            pid=self.pid,
            status=self.status,
            image=self.image,
            provider=provider,
        )

    def save(self) -> None:
        for db in self.get_db():
            db.merge(self.to_record())
            db.commit()

    @classmethod
    def from_record(cls, record: V1DesktopRecord) -> Desktop:
        out = cls.__new__(Desktop)
        out.id = record.id
        out.name = record.name
        out.addr = record.addr
        out.cpu = record.cpu
        out.created = record.created
        out.memory = record.memory
        out.disk = record.disk
        out.pid = record.pid
        out.status = record.status
        out.image = record.image
        if record.provider:
            dct = json.loads(record.provider)
            out.provider = V1ProviderData(**dct)
        return out

    @classmethod
    def load(cls, id: str) -> Desktop:
        for db in cls.get_db():
            record = db.query(V1DesktopRecord).filter(V1DesktopRecord.id == id).first()
            if record is None:
                raise ValueError(f"Desktop with id {id} not found")
            return cls.from_record(record)

    @classmethod
    def list(cls) -> list[Desktop]:
        out = []
        for db in cls.get_db():
            records = db.query(V1DesktopRecord).all()
            print("desktop records: ", records)
            for record in records:
                out.append(cls.from_record(record))
        return out

    @classmethod
    def list_v1(cls) -> V1Desktops:
        out = []
        for desktop in cls.list():
            out.append(desktop.to_v1_schema())
        return V1Desktops(desktops=out)

    @classmethod
    def delete(cls, id: str) -> None:
        for db in cls.get_db():
            record = db.query(V1DesktopRecord).filter(V1DesktopRecord.id == id).first()
            if record is None:
                raise ValueError(f"Desktop with id {id} not found")
            db.delete(record)
            db.commit()

    def to_v1_schema(self) -> V1Desktop:
        return V1Desktop(
            id=self.id,
            name=self.name,
            addr=self.addr,
            status=self.status,
            created=self.created,
            memory=self.memory,
            cpu=self.cpu,
            disk=self.disk,
            image=self.image,
            provider=self.provider,
        )

    def view(self) -> None:
        """Opens the desktop in a browser window"""

        check_command_availability("docker")

        host = get_docker_host()
        os.environ["DOCKER_HOST"] = host
        client = docker.from_env()

        exists = False
        for container in client.containers.list():
            print("a conatainer: ", container)
            if container.image.tags[0] == UI_IMG:
                exists = True
                print("using existing UI container")

        if not exists:
            print("creating UI container...")
            host_port = random.randint(1024, 65535)
            container = client.containers.run(
                UI_IMG, ports={3000: host_port}, detach=True
            )
        webbrowser.open(f"http://localhost:{host_port}")


DP = TypeVar("DP", bound="DesktopProvider")


class DesktopProvider(ABC):
    """A provider of desktop virtual machines"""

    @abstractmethod
    def create(
        self,
        name: str,
        image: str,
        memory: int = 4,
        cpu: int = 2,
        disk: str = "30gb",
        tags: List[str] = None,
        reserve_ip: bool = False,
        ssh_key: Optional[str] = None,
    ) -> Desktop:
        """Create a Desktop

        Args:
            name (str): Name of the VM
            image (str): Image of the VM
            memory (int): Memory allotment. Defaults to 4gb.
            cpu (int): CPU allotment. Defaults to 2.
            disk (str): Disk allotment. Defaults to 30gb.
            tags (List[str], optional): Tags to apply to the VM. Defaults to None.
            reserve_ip (bool, optional): Reserve an IP address. Defaults to False.
            ssh_key (str, optional): SSH key to use. Defaults to None.

        Returns:
            VM: A VM
        """
        pass

    @abstractmethod
    def delete(self, name: str) -> None:
        """Delete a VM

        Args:
            name (str): Name of the VM
        """
        pass

    @abstractmethod
    def start(self, name: str) -> None:
        """Start a VM

        Args:
            name (str): Name of the VM
        """
        pass

    @abstractmethod
    def stop(self, name: str) -> None:
        """Stop a VM

        Args:
            name (str): Name of the VM
        """
        pass

    @abstractmethod
    def list(self) -> List[Desktop]:
        """List VMs

        Returns:
            List[VM]: A list of VMs
        """
        pass

    @abstractmethod
    def get(self, name: str) -> None:
        """Get a VM

        Args:
            name (str): Name of the VM
        """
        pass

    @abstractmethod
    def to_data(self) -> V1ProviderData:
        """Convert to a ProviderData object

        Returns:
            ProviderData: ProviderData object
        """
        pass

    @classmethod
    @abstractmethod
    def from_data(cls, data: V1ProviderData) -> DP:
        """From provider data

        Args:
            data (ProviderData): Provider data
        """
        pass
