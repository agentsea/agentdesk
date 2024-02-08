from __future__ import annotations
from abc import ABC, abstractmethod
from typing import List, Optional, TypeVar, Any, Generic
import uuid
import time
import json
import webbrowser
import random
import os
import atexit

import docker
from docker.models.containers import Container

from agentdesk.db.conn import WithDB
from agentdesk.db.models import V1DesktopRecord
from agentdesk.server.models import V1Desktop, V1Desktops, V1ProviderData
from agentdesk.util import get_docker_host, check_command_availability
from agentdesk.proxy import ensure_ssh_proxy, cleanup_proxy

UI_IMG = "us-central1-docker.pkg.dev/agentsea-dev/agentdesk/ui:a85fde68ac9849d9301be702f2092a8a299abe52"


class DesktopVM(WithDB):
    """A remote desktop VM which is accesible for AI agents"""

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
        requires_proxy: bool = True,
        metadata: Optional[dict] = None,
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
        self.requires_proxy = requires_proxy
        self.metadata = metadata

        self.save()

    def to_record(self) -> V1DesktopRecord:
        provider = None
        if self.provider:
            provider = json.dumps(self.provider.__dict__)

        metadata = None
        if self.metadata:
            metadata = json.dumps(self.metadata)
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
            requires_proxy=self.requires_proxy,
            meta=metadata,
        )

    def save(self) -> None:
        for db in self.get_db():
            db.merge(self.to_record())
            db.commit()

    @classmethod
    def from_record(cls, record: V1DesktopRecord) -> DesktopVM:
        out = cls.__new__(DesktopVM)
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
        out.requires_proxy = record.requires_proxy
        if record.provider:
            dct = json.loads(record.provider)
            out.provider = V1ProviderData(**dct)
        if record.meta:
            dct = json.loads(record.meta)
            out.metadata = dct
        return out

    @classmethod
    def load(cls, id: str) -> DesktopVM:
        for db in cls.get_db():
            record = db.query(V1DesktopRecord).filter(V1DesktopRecord.id == id).first()
            if record is None:
                raise ValueError(f"Desktop with id {id} not found")
            return cls.from_record(record)

    @classmethod
    def find(cls, name: str) -> Optional[DesktopVM]:
        for db in cls.get_db():
            record = (
                db.query(V1DesktopRecord).filter(V1DesktopRecord.name == name).first()
            )
            if record is None:
                return None
            return cls.from_record(record)

    @classmethod
    def list(cls) -> list[DesktopVM]:
        out = []
        for db in cls.get_db():
            records = db.query(V1DesktopRecord).all()
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
            metadata=self.metadata,
        )

    def view(self, background: bool = False) -> None:
        """Opens the desktop in a browser window"""
        if self.requires_proxy:
            proxy_pid = ensure_ssh_proxy(6080, "agentsea", "localhost")
        check_command_availability("docker")

        host = get_docker_host()
        os.environ["DOCKER_HOST"] = host
        client = docker.from_env()

        host_port = None
        ui_container: Optional[Container] = None

        for container in client.containers.list():
            print("a container: ", container)
            if container.image.tags[0] == UI_IMG:
                print("using existing UI container")
                # Retrieve the host port for the existing container
                host_port = container.attrs["NetworkSettings"]["Ports"]["3000/tcp"][0][
                    "HostPort"
                ]
                ui_container = container
                break

        if not ui_container:
            print("creating UI container...")
            host_port = random.randint(1024, 65535)
            ui_container = client.containers.run(
                UI_IMG, ports={"3000/tcp": host_port}, detach=True
            )
            print("waiting for UI container to start...")
            time.sleep(10)

        webbrowser.open(f"http://localhost:{host_port}")

        if background:
            return

        def onexit():
            print("stopping UI container...")
            ui_container.stop()
            print("removing UI container...")
            ui_container.remove()
            print("stopping ssh proxy...")
            cleanup_proxy(proxy_pid)

        atexit.register(onexit)
        while True:
            print(f"proxying desktop vnc '{self.name}' to localhost:6080...")
            time.sleep(20)


DP = TypeVar("DP", bound="DesktopProvider")


class DesktopProvider(ABC, Generic[DP]):
    """A provider of desktop virtual machines"""

    @abstractmethod
    def create(
        self,
        name: Optional[str] = None,
        image: Optional[str] = None,
        memory: int = 4,
        cpu: int = 2,
        disk: str = "30gb",
        tags: List[str] = None,
        reserve_ip: bool = False,
        ssh_key: Optional[str] = None,
    ) -> DesktopVM:
        """Create a Desktop

        Args:
            name (str, optional): Name of the VM. Defaults to random generation.
            image (str, optional): Image of the VM. Defaults to Ubuntu Jammy.
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
    def list(self) -> List[DesktopVM]:
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
