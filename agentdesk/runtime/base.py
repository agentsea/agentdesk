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
from pathlib import Path
import base64

import docker
from docker.models.containers import Container
from cryptography.fernet import Fernet
import shortuuid

from agentdesk.db.conn import WithDB
from agentdesk.db.models import V1DesktopRecord
from agentdesk.server.models import V1DesktopInstance, V1ProviderData
from agentdesk.util import (
    get_docker_host,
    check_command_availability,
    check_port_in_use,
)
from agentdesk.proxy import ensure_ssh_proxy, cleanup_proxy
from agentdesk.key import SSHKeyPair


UI_IMG = "us-central1-docker.pkg.dev/agentsea-dev/agentdesk/ui:634820941cbbba4b3cd51149b25d0a4c8d1a35f4"


class DesktopInstance(WithDB):
    """A remote desktop VM which is accesible for AI agents"""

    def __init__(
        self,
        name: str,
        addr: Optional[str] = None,
        id: Optional[str] = None,
        cpu: Optional[int] = None,
        memory: Optional[int] = None,
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
        key_pair_name: Optional[str] = None,
        agentd_port: int = 8000,
        vnc_port: Optional[int] = None,
        vnc_port_https: Optional[int] = None,
        basic_auth_user: Optional[str] = None,
        basic_auth_password: Optional[str] = None,
        resource_name: Optional[str] = None,
        namespace: Optional[str] = None,
    ) -> None:
        if not id:
            id = shortuuid.uuid()
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
        self.key_pair_name = key_pair_name
        self.agentd_port = agentd_port
        self.vnc_port = vnc_port
        self.vnc_port_https = vnc_port_https
        self.basic_auth_user = basic_auth_user
        self.basic_auth_password = basic_auth_password
        self.resource_name = resource_name
        self.namespace = namespace

        self.save()

    @classmethod
    def get_encryption_key(cls) -> bytes:
        # Step 1: Try to get the key from an environment variable
        key = os.getenv("ENCRYPTION_KEY")
        if key:
            return key.encode()

        # Define the path for the local encryption key file
        key_path = Path.home() / ".agentsea/keys/agentdesk_encryption_key"

        # Step 2: Try to get the key from a local file
        try:
            if key_path.exists():
                with key_path.open("rb") as file:
                    return file.read()
        except IOError as e:
            print(f"Failed to read the encryption key from {key_path}: {e}")

        print(
            "No encryption key found. Generating a new one. "
            "This key will be stored in ~/.agentsea/keys/agentdesk_encryption_key"
        )
        # Step 3: Generate a new key and store it if neither of the above worked
        key = Fernet.generate_key()
        try:
            key_path.parent.mkdir(
                parents=True, exist_ok=True
            )  # Ensure the directory exists
            with key_path.open("wb") as file:
                file.write(key)
        except IOError as e:
            print(f"Failed to write the new encryption key to {key_path}: {e}")
            raise Exception("Failed to secure an encryption key.")

        return key

    def encrypt_password(self, password: str) -> str:
        key = self.get_encryption_key()
        fernet = Fernet(key)
        encrypted_password = fernet.encrypt(password.encode())
        return base64.b64encode(encrypted_password).decode()

    @classmethod
    def decrypt_password(cls, encrypted_password: str) -> str:
        key = cls.get_encryption_key()
        fernet = Fernet(key)
        decrypted_password = fernet.decrypt(base64.b64decode(encrypted_password))
        return decrypted_password.decode()

    def to_record(self) -> V1DesktopRecord:
        provider = None
        if self.provider:
            provider = json.dumps(self.provider.__dict__)

        metadata = None
        if self.metadata:
            metadata = json.dumps(self.metadata)

        basic_auth_password = None
        if self.basic_auth_password:
            basic_auth_password = self.encrypt_password(self.basic_auth_password)

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
            key_pair_name=self.key_pair_name,
            agentd_port=self.agentd_port,
            vnc_port=self.vnc_port,
            vnc_port_https=self.vnc_port_https,
            basic_auth_user=self.basic_auth_user,
            basic_auth_password=basic_auth_password,
            resource_name=self.resource_name,
            namespace=self.namespace,
        )

    def save(self) -> None:
        for db in self.get_db():
            try:
                record = self.to_record()
                db.merge(record)
                db.commit()
            except Exception as e:
                db.rollback()
                print(f"Error saving DesktopInstance: {e}")
                raise

    @classmethod
    def from_record(cls, record: V1DesktopRecord) -> DesktopInstance:
        out = cls.__new__(DesktopInstance)  # type: ignore
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
        out.key_pair_name = record.key_pair_name
        out.agentd_port = record.agentd_port
        out.vnc_port = record.vnc_port
        out.vnc_port_https = record.vnc_port_https
        out.resource_name = record.resource_name
        out.namespace = record.namespace
        if record.provider:  # type: ignore
            dct = json.loads(str(record.provider))
            out.provider = V1ProviderData(**dct)
        out.metadata = {}
        if record.meta:  # type: ignore
            dct = json.loads(str(record.meta))
            out.metadata = dct

        out.basic_auth_password = None
        if record.basic_auth_password:  # type: ignore
            out.basic_auth_password = cls.decrypt_password(
                str(record.basic_auth_password)
            )
        out.basic_auth_user = record.basic_auth_user
        return out

    @classmethod
    def load(cls, id: str) -> DesktopInstance:
        for db in cls.get_db():
            record = db.query(V1DesktopRecord).filter(V1DesktopRecord.id == id).first()
            if record is None:
                raise ValueError(f"Desktop with id {id} not found")
            return cls.from_record(record)
        raise ValueError("no session")

    @classmethod
    def get(
        cls, name: str, owner_id: Optional[str] = None
    ) -> Optional[DesktopInstance]:
        for db in cls.get_db():
            record = (
                db.query(V1DesktopRecord)
                .filter_by(name=name, owner_id=owner_id)
                .first()
            )
            if record is None:
                return None
            return cls.from_record(record)

    @classmethod
    def find(cls, **kwargs) -> List[DesktopInstance]:
        """Find desktops by given keyword arguments."""
        out = []
        for db in cls.get_db():
            records = db.query(V1DesktopRecord).filter_by(**kwargs).all()
            for record in records:
                out.append(cls.from_record(record))
        return out

    @classmethod
    def find_v1(cls, **kwargs) -> List[V1DesktopInstance]:
        """Find desktops by given keyword arguments."""
        out = []
        for db in cls.get_db():
            records = db.query(V1DesktopRecord).filter_by(**kwargs).all()
            for record in records:
                out.append(cls.from_record(record).to_v1_schema())
        return out

    def delete(self, force: bool = False) -> None:
        try:
            if not self.provider:
                raise ValueError(f"Desktop with id {self.id} not found")

            if self.provider.type == "kube":
                from .kube import KubernetesProvider, KubeConnectConfig

                if not self.provider.args:
                    raise ValueError(
                        f"No args for kube provider while deleting {self.id}"
                    )

                cfg = KubeConnectConfig.model_validate_json(self.provider.args["cfg"])
                provider = KubernetesProvider(cfg=cfg)

                provider.delete(self.name)

            elif self.provider.type == "docker":
                from .docker import DockerProvider, DockerConnectConfig

                if not self.provider.args:
                    raise ValueError(
                        f"No args for kube provider while deleting {self.id}"
                    )

                cfg = DockerConnectConfig.model_validate_json(self.provider.args["cfg"])
                provider = DockerProvider(cfg=cfg)

                provider.delete(self.name)

            elif self.provider.type == "ec2":
                from .ec2 import EC2Provider

                if not self.provider.args:
                    raise ValueError(
                        f"No args for kube provider while deleting {self.id}"
                    )

                provider = EC2Provider.from_data(self.provider)
                provider.delete(self.name)

            elif self.provider.type == "gce":
                from .gce import GCEProvider

                if not self.provider.args:
                    raise ValueError(
                        f"No args for kube provider while deleting {self.id}"
                    )

                provider = GCEProvider.from_data(self.provider)
                provider.delete(self.name)

            elif self.provider.type == "qemu":
                from .qemu import QemuProvider

                if not self.provider.args:
                    raise ValueError(
                        f"No args for kube provider while deleting {self.id}"
                    )
                provider = QemuProvider.from_data(self.provider)
                provider.delete(self.name)

            else:
                raise ValueError(f"Unknown provider type: {self.provider.type}")

        except Exception as e:
            if not force:
                raise e

        for db in self.get_db():
            try:
                record = (
                    db.query(V1DesktopRecord)
                    .filter(V1DesktopRecord.id == self.id)
                    .first()
                )
                if record is None:
                    raise ValueError(f"Desktop with id {self.id} not found")

                db.delete(record)
                db.commit()
            except Exception as e:
                pass

    @classmethod
    def name_exists(cls, name: str, owner_id: Optional[str] = None) -> bool:
        for db in cls.get_db():
            record = (
                db.query(V1DesktopRecord)
                .filter_by(name=name, owner_id=owner_id)
                .first()
            )
            if record is None:
                return False

            return True

        raise ValueError("no session")

    def remove(self) -> None:
        for db in self.get_db():
            record = (
                db.query(V1DesktopRecord).filter(V1DesktopRecord.id == self.id).first()
            )
            if record is None:
                raise ValueError(f"Desktop with id {self.id} not found")
            db.delete(record)
            db.commit()

    def to_v1_schema(self) -> V1DesktopInstance:
        return V1DesktopInstance(
            id=self.id,
            name=self.name,
            addr=self.addr,
            status=self.status,
            created=self.created,
            memory=self.memory,  # type: ignore
            cpu=self.cpu,
            disk=self.disk,
            image=self.image,
            reserved_ip=self.reserved_ip,
            provider=self.provider,
            meta=self.metadata,
            owner_id=self.owner_id,
            key_pair_name=self.key_pair_name,
            agentd_port=self.agentd_port,
            vnc_port=self.vnc_port,
            vnc_port_https=self.vnc_port_https,
            basic_auth_user=self.basic_auth_user,
            basic_auth_password=self.basic_auth_password,
            resource_name=self.resource_name,
            namespace=self.namespace,
        )

    def view(
        self,
        background: bool = False,
        bind_addr: str = "127.0.0.1",
        browser: bool = True,
    ) -> None:
        """Opens the desktop in a browser window"""

        if self.provider and self.provider.type in ["docker"]:
            webbrowser.open(f"http://localhost:{self.vnc_port}")
            return

        elif self.provider and self.provider.type in ["kube"]:
            # TODO: add support for kube
            from .kube import KubernetesProvider, KubeConnectConfig

            if not self.provider.args:
                raise ValueError(f"No args for kube provider while deleting {self.id}")

            cfg = KubeConnectConfig.model_validate_json(self.provider.args["cfg"])
            provider = KubernetesProvider(cfg=cfg)

            local_port, _ = provider.proxy(self.name)
            print(f"Proxy created on port {local_port}")

            time.sleep(2)
            webbrowser.open(f"http://localhost:{local_port}")
            input("Press any key to exit...")
            return

        if self.requires_proxy:
            keys = SSHKeyPair.find(name=self.key_pair_name)
            if not keys:
                raise ValueError(
                    f"No key pair found with name {self.key_pair_name} and is required for this desktop"
                )
            key_pair = keys[0]

            if check_port_in_use(6080):
                raise ValueError(
                    "Port 6080 is already in use, UI requires this port"
                )  # TODO: remove this restriction
            proxy_pid = ensure_ssh_proxy(
                6080,
                6080,
                self.ssh_port,
                "agentsea",
                self.addr,  # type: ignore  # TODO: replace with proxy()
                key_pair.decrypt_private_key(key_pair.private_key),
                bind_addr=bind_addr,
            )
            atexit.register(cleanup_proxy, proxy_pid)

        check_command_availability("docker")

        host = get_docker_host()
        os.environ["DOCKER_HOST"] = host
        client = docker.from_env()

        host_port = None
        ui_container: Optional[Container] = None

        for container in client.containers.list():
            if container.image.tags[0] == UI_IMG:  # type: ignore
                print("found running UI container")
                # Retrieve the host port for the existing container
                host_port = container.attrs["NetworkSettings"]["Ports"]["3000/tcp"][0][  # type: ignore
                    "HostPort"
                ]
                ui_container = container  # type: ignore
                break

        if not ui_container:
            print("creating UI container...")
            host_port = random.randint(1024, 65535)
            ui_container = client.containers.run(  # type: ignore
                UI_IMG, ports={"3000/tcp": host_port}, detach=True
            )
            print("waiting for UI container to start...")
            time.sleep(10)

        if browser:
            webbrowser.open(f"http://localhost:{host_port}")
        else:
            print(f"\n>>> UI available at http://localhost:{host_port}\n")

        if background:
            return

        def onexit():
            nonlocal proxy_pid
            print("Cleaning up resources...")

            # Check if the UI container still exists and stop/remove it if so
            if ui_container:
                try:
                    container_status = client.containers.get(ui_container.id).status  # type: ignore
                    if container_status in ["running", "paused"]:
                        print("stopping UI container...")
                        ui_container.stop()
                        print("removing UI container...")
                        ui_container.remove()
                except docker.errors.NotFound:  # type: ignore
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
                print(f"proxying desktop vnc '{self.name}' to {bind_addr}:6080...")
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
        ssh_key_pair: Optional[str] = None,
        owner_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        generate_password: bool = False,
        sub_folder: Optional[str] = None,
        id: Optional[str] = None,
    ) -> DesktopInstance:
        """Create a Desktop

        Args:
            name (str, optional): Name of the VM. Defaults to random generation.
            image (str, optional): Image of the VM. Defaults to Ubuntu Jammy.
            memory (int): Memory allotment. Defaults to 4gb.
            cpu (int): CPU allotment. Defaults to 2.
            disk (str): Disk allotment. Defaults to 30gb.
            tags (List[str], optional): Tags to apply to the VM. Defaults to None.
            reserve_ip (bool, optional): Reserve an IP address. Defaults to False.
            ssh_key_pair (str, optional): SSH key pair name to use. Defaults to None.
            owner_id (str, optional): Owner of the VM. Defaults to None.
            metadata (Dict[str, Any], optional): Metadata to apply to the VM. Defaults to None.
            generate_password (bool, optional): Generate a password for the VM. Defaults to False.
            sub_folder (str, optional): Sub folder to use. Defaults to None.
            id (str, optional): ID of the instance. Defaults to None.

        Returns:
            DesktopInstance: A desktop instance
        """
        pass

    @abstractmethod
    def delete(self, name: str, owner_id: Optional[str] = None) -> None:
        """Delete a VM

        Args:
            name (str): Name of the VM
            owner_id (str, optional): Owner of the VM. Defaults to None
        """
        pass

    @abstractmethod
    def start(
        self,
        name: str,
        private_ssh_key: Optional[str] = None,
        owner_id: Optional[str] = None,
    ) -> None:
        """Start a VM

        Args:
            name (str): Name of the VM
            private_ssh_key (str, optional): SSH key to use. Defaults to use ~/.ssh/id_rsa.
        """
        pass

    @abstractmethod
    def stop(self, name: str, owner_id: Optional[str] = None) -> None:
        """Stop a VM

        Args:
            name (str): Name of the VM
            owner_id (str, optional): Owner of the VM. Defaults to None
        """
        pass

    @abstractmethod
    def list(self) -> List[DesktopInstance]:
        """List VMs

        Returns:
            List[VM]: A list of VMs
        """
        pass

    @abstractmethod
    def get(
        self, name: str, owner_id: Optional[str] = None
    ) -> Optional[DesktopInstance]:
        """Get a VM

        Args:
            name (str): Name of the VM
            owner_id (str, optional): Owner of the VM. Defaults to None
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
