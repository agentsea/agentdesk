from __future__ import annotations
from abc import ABC, abstractmethod
from typing import List, Optional, TypeVar, Generic, Dict, Any
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
from agentdesk.server.models import V1Desktop, V1ProviderData
from agentdesk.util import (
    get_docker_host,
    check_command_availability,
    check_port_in_use,
)
from agentdesk.proxy import ensure_ssh_proxy, cleanup_proxy


UI_IMG = "us-central1-docker.pkg.dev/agentsea-dev/agentdesk/ui:634820941cbbba4b3cd51149b25d0a4c8d1a35f4"


class DesktopVM(WithDB):
    """A remote desktop VM which is accesible for AI agents"""

    def __init__(
        self,
        name: str,
        addr: str,
        id: Optional[str] = None,
        cpu: Optional[int] = None,
        memory: Optional[str] = None,
        disk: Optional[str] = None,
        pid: Optional[int] = None,
        status: str = "running",
        image: Optional[str] = None,
        provider: Optional[V1ProviderData] = None,
        reserved_ip: bool = False,
        requires_proxy: bool = True,
        metadata: Optional[dict] = None,
        ssh_port: int = 22,
        owner_id: Optional[str] = None,
        ssh_key: Optional[str] = None,
    ) -> None:
        if not id:
            id = str(uuid.uuid4())
        self.name = name
        self.addr = addr
        self.cpu = cpu
        self.memory = memory
        self.disk = disk
        self.pid = pid
        self.id = id
        self.created = time.time()
        self.status = status
        self.image = image
        self.provider = provider
        self.reserved_ip = reserved_ip
        self.requires_proxy = requires_proxy
        self.metadata = metadata
        self.ssh_port = ssh_port
        self.owner_id = owner_id
        self.ssh_key = ssh_key

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
            reserved_ip=self.reserved_ip,
            requires_proxy=self.requires_proxy,
            ssh_port=self.ssh_port,
            meta=metadata,
            owner_id=self.owner_id,
            ssh_key=self.ssh_key,
        )

    def save(self) -> None:
        for db in self.get_db():
            record = self.to_record()
            db.merge(record)
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
        out.reserved_ip = record.reserved_ip
        out.requires_proxy = record.requires_proxy
        out.ssh_port = record.ssh_port
        out.owner_id = record.owner_id
        out.ssh_key = record.ssh_key
        if record.provider:
            dct = json.loads(record.provider)
            out.provider = V1ProviderData(**dct)
        out.metadata = {}
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
    def get(cls, name: str) -> Optional[DesktopVM]:
        for db in cls.get_db():
            record = (
                db.query(V1DesktopRecord).filter(V1DesktopRecord.name == name).first()
            )
            if record is None:
                return None
            return cls.from_record(record)

    @classmethod
    def find(cls, **kwargs) -> List[DesktopVM]:
        """Find desktops by given keyword arguments."""
        out = []
        for db in cls.get_db():
            records = db.query(V1DesktopRecord).filter_by(**kwargs).all()
            for record in records:
                out.append(cls.from_record(record))
        return out

    @classmethod
    def find_v1(cls, **kwargs) -> List[V1Desktop]:
        """Find desktops by given keyword arguments."""
        out = []
        for db in cls.get_db():
            records = db.query(V1DesktopRecord).filter_by(**kwargs).all()
            for record in records:
                out.append(cls.from_record(record).to_v1_schema())
        return out

    @classmethod
    def delete(cls, id: str) -> None:
        for db in cls.get_db():
            record = db.query(V1DesktopRecord).filter(V1DesktopRecord.id == id).first()
            if record is None:
                raise ValueError(f"Desktop with id {id} not found")
            db.delete(record)
            db.commit()

    @classmethod
    def name_exists(cls, name: str) -> bool:
        for db in cls.get_db():
            record = (
                db.query(V1DesktopRecord).filter(V1DesktopRecord.name == name).first()
            )
            if record is None:
                return False

            return True

    def remove(self) -> None:
        for db in self.get_db():
            record = (
                db.query(V1DesktopRecord).filter(V1DesktopRecord.id == self.id).first()
            )
            if record is None:
                raise ValueError(f"Desktop with id {self.id} not found")
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
            reserved_ip=self.reserved_ip,
            provider=self.provider,
            metadata=self.metadata,
            ssh_port=self.ssh_port,
            owner_id=self.owner_id,
        )

    def view(self, background: bool = False) -> None:
        """Opens the desktop in a browser window"""

        if self.requires_proxy:
            if check_port_in_use(6080):
                raise ValueError(
                    "Port 6080 is already in use, UI requires this port"
                )  # TODO: remove this restriction
            proxy_pid = ensure_ssh_proxy(
                6080, 6080, self.ssh_port, "agentsea", self.addr
            )
            atexit.register(cleanup_proxy, proxy_pid)

        check_command_availability("docker")

        host = get_docker_host()
        os.environ["DOCKER_HOST"] = host
        client = docker.from_env()

        host_port = None
        ui_container: Optional[Container] = None

        for container in client.containers.list():
            if container.image.tags[0] == UI_IMG:
                print("found running UI container")
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
            nonlocal proxy_pid
            print("Cleaning up resources...")

            # Check if the UI container still exists and stop/remove it if so
            if ui_container:
                try:
                    container_status = client.containers.get(ui_container.id).status
                    if container_status in ["running", "paused"]:
                        print("stopping UI container...")
                        ui_container.stop()
                        print("removing UI container...")
                        ui_container.remove()
                except docker.errors.NotFound:
                    print("UI container already stopped/removed.")

            # Stop the SSH proxy if required and not already stopped
            if self.requires_proxy and proxy_pid:
                try:
                    print("stopping ssh proxy...")
                    cleanup_proxy(proxy_pid)
                except Exception as e:
                    print(f"Error stopping SSH proxy: {e}")
                finally:
                    proxy_pid = None  # Ensure we don't try to stop it again

        atexit.register(onexit)
        try:
            while True:
                print(f"proxying desktop vnc '{self.name}' to localhost:6080...")
                time.sleep(20)
        except KeyboardInterrupt:
            print("Keyboard interrupt received, exiting...")
            onexit()


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
        tags: Optional[Dict[str, str]] = None,
        reserve_ip: bool = False,
        ssh_key: Optional[str] = None,
        owner_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
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
            owner_id (str, optional): Owner of the VM. Defaults to None.
            metadata (Dict[str, Any], optional): Metadata to apply to the VM. Defaults to None.

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
    def get(self, name: str) -> Optional[DesktopVM]:
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

    @abstractmethod
    def refresh(self, log: bool = True) -> None:
        """Refresh state"""
        pass
