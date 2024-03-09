# from __future__ import annotations
import base64
import io
from enum import Enum
import time
import os
from typing import Tuple, Optional, List, Any
import atexit

import requests

from PIL import Image
from google.cloud import storage
from opentool import Tool, action, observation, Action

from .vm.base import DesktopVM, DesktopProvider

try:
    from .vm.gce import GCEProvider
except ImportError:
    print(
        "GCE provider unavailable, install with `pip install agentdesk[gce] if desired"
    )
try:
    from .vm.ec2 import EC2Provider
except ImportError:
    print(
        "AWS provider unavailable, install with `pip install agentdesk[aws] if desired"
    )

from .vm.qemu import QemuProvider
from .util import (
    extract_file_path,
    extract_gcs_info,
    generate_random_string,
    check_port_in_use,
)
from .proxy import ensure_ssh_proxy, cleanup_proxy


class StorageStrategy(Enum):
    GCS = "gcs"
    LOCAL = "local"


class Desktop(Tool):
    """Desktop OS as a tool via agentd"""

    def __init__(
        self,
        agentd_url: Optional[str] = None,
        vm: Optional[DesktopVM] = None,
        storage_uri: str = "file://.media",
        type_min_interval: float = 0.05,
        type_max_interval: float = 0.25,
        move_mouse_duration: float = 1.0,
        mouse_tween: str = "easeInOutQuad",
        store_img: bool = False,
        requires_proxy: bool = False,
        proxy_type: str = "process",
        proxy_port: int = 8000,
    ) -> None:
        """Connect to an agent desktop

        Args:
            agentd_url (str, optional): URL of a running agentd server. Defaults to None.
            vm (str, optional): Optional desktop VM to use. Defaults to None.
            storage_uri (str): The directory where to store images or videos taken of the VM, supports gs:// or file://. Defaults to file://.media.
            type_min_interval (float, optional): Min interval between pressing next key. Defaults to 0.05.
            type_max_interval (float, optional): Max interval between pressing next key. Defaults to 0.25.
            move_mouse_duration (float, optional): How long should it take to move. Defaults to 1.0.
            mouse_tween (str, optional): The movement tween. Defaults to "easeInOutQuad".
            store_img (bool, optional): Whether to store the image in the cloud. Defaults to false
            requires_proxy (bool, optional): Whether to use a proxy. Defaults to False.
            proxy_type (str, optional): The type of proxy to use. Defaults to process.
            proxy_port (int, optional): The port to use for the proxy. Defaults to 8000.
        """
        super().__init__()
        self._vm = vm

        if vm:
            if vm.requires_proxy:
                self.base_url = f"localhost:{proxy_port}"
            else:
                self.base_url = f"{vm.addr}:8000"
        else:
            self.base_url = agentd_url

        if not self.base_url.startswith("http"):
            self.base_url = f"http://{self.base_url}"

        self.storage_uri = storage_uri
        self._type_min_interval = type_min_interval
        self._type_max_interval = type_max_interval
        self._move_mouse_duration = move_mouse_duration
        self._mouse_tween = mouse_tween
        self._store_img = store_img
        self._proxy_port = proxy_port
        self._requires_proxy = vm.requires_proxy or requires_proxy

        if self._requires_proxy:
            if proxy_type == "process":
                print("starting proxy to vm...")
                if check_port_in_use(proxy_port):
                    raise ValueError(
                        f"Port {proxy_port} is already in use"
                    )  # TODO: remove this restriction
                proxy_pid = ensure_ssh_proxy(
                    proxy_port, 8000, vm.ssh_port, "agentsea", vm.addr
                )
                atexit.register(cleanup_proxy, proxy_pid)
                print("proxy from port 8000 to port 8000 started...")
        else:
            print("vm doesn't require proxy")

        try:
            resp = self.health()
            if resp["status"] != "ok":
                raise ValueError("agentd status is not ok")
        except Exception as e:
            raise SystemError(f"could not connect to desktop, is agentd running? {e}")

        print("connected to desktop via agentd")

    @classmethod
    def ensure(
        cls,
        name: str,
        provider: DesktopProvider = QemuProvider(),
        image: Optional[str] = None,
        memory: int = 4,
        cpus: int = 2,
        disk: str = "30gb",
        reserve_ip: bool = False,
        ssh_key: Optional[str] = None,
    ) -> "Desktop":
        """Find or create a desktop"""
        vm = DesktopVM.get(name)
        if vm:
            return cls.from_vm(vm)

        return cls.create(
            name, provider, image, memory, cpus, disk, reserve_ip, ssh_key
        )

    @classmethod
    def create(
        cls,
        name: Optional[str] = None,
        provider: DesktopProvider = QemuProvider(),
        image: Optional[str] = None,
        memory: int = 4,
        cpus: int = 2,
        disk: str = "30gb",
        reserve_ip: bool = False,
        ssh_key: Optional[str] = None,
    ) -> "Desktop":
        """Create a desktop VM"""
        vm = provider.create(name, image, memory, cpus, disk, reserve_ip, ssh_key)
        return cls.from_vm(vm)

    @classmethod
    def ec2(
        cls,
        name: Optional[str] = None,
        region: Optional[str] = None,
        image: Optional[str] = None,
        memory: int = 4,
        cpus: int = 2,
        disk: str = "30gb",
        reserve_ip: bool = False,
        ssh_key: Optional[str] = None,
    ) -> "Desktop":
        """Create a desktop VM on EC2"""
        return cls.create(
            name=name,
            provider=EC2Provider(region),
            image=image,
            memory=memory,
            cpus=cpus,
            disk=disk,
            reserve_ip=reserve_ip,
            ssh_key=ssh_key,
        )

    @classmethod
    def gce(
        cls,
        name: Optional[str] = None,
        project: Optional[str] = None,
        zone: Optional[str] = None,
        image: Optional[str] = None,
        memory: int = 4,
        cpus: int = 2,
        disk: str = "30gb",
        reserve_ip: bool = False,
        ssh_key: Optional[str] = None,
    ) -> "Desktop":
        """Create a desktop VM on GCE"""
        return cls.create(
            name=name,
            provider=GCEProvider(project, zone),
            image=image,
            memory=memory,
            cpus=cpus,
            disk=disk,
            reserve_ip=reserve_ip,
            ssh_key=ssh_key,
        )

    @classmethod
    def local(
        cls,
        name: Optional[str] = None,
        memory: int = 4,
        cpus: int = 2,
    ) -> "Desktop":
        """Create a local VM

        Args:
            name (str, optional): Name of the vm. Defaults to None.
            memory (int, optional): Memory the VM has. Defaults to 4.
            cpus (int, optional): CPUs the VM has. Defaults to 2.

        Returns:
            Desktop: A desktop
        """
        return cls.create(name=name, provider=QemuProvider(), memory=memory, cpus=cpus)

    @classmethod
    def from_vm(
        cls, vm: DesktopVM, proxy_type: str = "process", proxy_port: int = 8000
    ) -> "Desktop":
        """Create a desktop from a VM

        Args:
            vm (DesktopVM): VM to use
            proxy_type (str, optional): The type of proxy to use. Defaults to process.
            proxy_port (int, optional): The port to use for the proxy. Defaults to 8000.

        Returns:
            Desktop: A desktop
        """
        return Desktop(vm=vm, proxy_type=proxy_type, proxy_port=8000)

    @classmethod
    def get(cls, name: str) -> Optional[DesktopVM]:
        """Get a desktop by name

        Args:
            name (str): Name of the desktop

        Returns:
            Desktop: A desktop
        """
        return DesktopVM.get(name)

    @classmethod
    def find(cls, **kwargs: Any) -> list[DesktopVM]:
        """List all desktops

        Returns:
            list[DesktopVM]: A list of desktop vms
        """
        return DesktopVM.find(**kwargs)

    def info(self) -> dict:
        """Get info on the desktop runtime

        Returns:
            dict: A dictionary of info
        """
        response = requests.get(f"{self.base_url}/info")
        return response.json()

    def view(self, background: bool = False) -> None:
        """View the desktop

        Args:
            background (bool, optional): Whether to run in the background and not block. Defaults to False.
        """

        if not self._vm:
            raise ValueError("Desktop not created with a VM, don't know how to proxy")

        self._vm.view(background)

    def health(self) -> dict:
        """Health of agentd

        Returns:
            dict: Agentd health
        """
        response = requests.get(f"{self.base_url}/health")
        return response.json()

    @action
    def open_url(self, url: str) -> None:
        """Open a URL in chromium

        Args:
            url (str): URL to open
        """
        requests.post(f"{self.base_url}/open_url", json={"url": url})
        return

    @action
    def move_mouse(self, x: int, y: int) -> None:
        """Move mouse to a position

        Args:
            x (int): x coordinate
            y (int): y coordinate
        """
        requests.post(
            f"{self.base_url}/move_mouse",
            json={
                "x": x,
                "y": y,
                "duration": self._move_mouse_duration,
                "tween": self._mouse_tween,
            },
        )
        return

    @action
    def click(
        self, button: str = "left", x: Optional[int] = None, y: Optional[int] = None
    ) -> None:
        """Click mouse button

        Args:
            button (str, optional): Button to click. Defaults to "left".
            x (Optional[int], optional): X coordinate to move to, if not provided it will click on current location. Defaults to None.
            y (Optional[int], optional): Y coordinate to move to, if not provided it will click on current location. Defaults to None.
        """
        body = {"button": button}
        if x and y:
            body["location"] = {"x": x, "y": y}

        requests.post(f"{self.base_url}/click", json=body)
        return

    @action
    def press_key(self, key: str) -> None:
        """Press a key

        Args:
            key (str): Which key to press
        """
        requests.post(f"{self.base_url}/press_key", json={"key": key})
        return

    @action
    def scroll(self, clicks: int = -3) -> None:
        """Scroll the screen

        Args:
            clicks (int, optional): Number of clicks, negative scrolls down, positive scrolls up. Defaults to -3.
        """
        requests.post(f"{self.base_url}/scroll", json={"clicks": clicks})
        return

    @action
    def drag_mouse(self, x: int, y: int) -> None:
        """Drag the mouse

        Args:
            x (int): x coordinate
            y (int): y coordinate
        """
        requests.post(f"{self.base_url}/drag_mouse", json={"x": x, "y": y})
        return

    @action
    def double_click(self) -> None:
        """Double click the mouse"""
        requests.post(f"{self.base_url}/double_click")
        return

    @action
    def type_text(self, text: str) -> None:
        """Type text

        Args:
            text (str): Text to type
        """
        requests.post(
            f"{self.base_url}/type_text",
            json={
                "text": text,
                "min_interval": self._type_min_interval,
                "max_interval": self._type_max_interval,
            },
        )
        return

    @observation
    def take_screenshot(self) -> str:
        """Take screenshot

        Returns:
            str: b64 encoded image or URI of the image depending on instance settings
        """
        response = requests.post(f"{self.base_url}/screenshot")
        jdict = response.json()

        if not self._store_img:
            return jdict["image"]

        image_data = base64.b64decode(jdict["image"])
        image_stream = io.BytesIO(image_data)
        image = Image.open(image_stream)

        filename = f"screen-{int(time.time())}-{generate_random_string()}.png"

        if self.storage_uri.startswith("file://"):
            filepath = extract_file_path(self.storage_uri)
            save_path = os.path.join(filepath, filename)
            image.save(save_path)
            return save_path

        elif self.storage_uri.startswith("gs://"):
            bucket_name, object_path = extract_gcs_info(self.storage_uri)
            object_path = os.path.join(object_path, filename)

            client = storage.Client()
            bucket = client.bucket(bucket_name)
            blob = bucket.blob(object_path)
            blob.upload_from_string(image_data, content_type="image/png")

            blob.make_public()
            return blob.public_url
        else:
            raise ValueError("Invalid store_type. Choose 'file' or 'gcs'.")

    @observation
    def mouse_coordinates(self) -> Tuple[int, int]:
        """Get the current mouse coordinates

        Returns:
            Tuple[int, int]: x, y coordinates
        """
        response = requests.get(f"{self.base_url}/mouse_coordinates")
        jdict = response.json()

        return jdict["x"], jdict["y"]

    def close(self):
        pass


class SimpleDesktop(Desktop):
    """A more simple desktop"""

    def actions(self) -> List[Action]:
        """Actions the agent can take

        Returns:
            List[Action]: List of actions
        """
        out = []
        for actionv in self._actions_list:
            if actionv.name in [
                "open_url",
                "type_text",
                "click",
                "scroll",
                "press_key",
                "move_mouse",
            ]:
                out.append(actionv)

        return out


class WebApp(Desktop):
    """A web application running on a desktop vm"""

    def __init__(
        self,
        url: str,
        agentd_url: Optional[str] = None,
        vm: Optional[DesktopVM] = None,
        storage_uri: str = "file://.media",
        type_min_interval: float = 0.05,
        type_max_interval: float = 0.25,
        move_mouse_duration: float = 1.0,
        mouse_tween: str = "easeInOutQuad",
        store_img: bool = False,
    ) -> None:
        """
        Initialize and open a URL in the application.

        Args:
            url: URL to open upon initialization.
            agentd_url: URL of a running agentd server. Defaults to None.
            vm: Optional desktop VM to use. Defaults to None.
            storage_uri: The directory where to store images or videos taken of the VM, supports gs:// or file://. Defaults to "file://.media".
            type_min_interval: Min interval between pressing the next key. Defaults to 0.05.
            type_max_interval: Max interval between pressing the next key. Defaults to 0.25.
            move_mouse_duration: How long it should take to move the mouse. Defaults to 1.0.
            mouse_tween: The movement tween. Defaults to "easeInOutQuad".
            store_img: Whether to store the image in the cloud. Defaults to False.
        """
        super().__init__(
            agentd_url=agentd_url,
            vm=vm,
            storage_uri=storage_uri,
            type_min_interval=type_min_interval,
            type_max_interval=type_max_interval,
            move_mouse_duration=move_mouse_duration,
            mouse_tween=mouse_tween,
            store_img=store_img,
        )
        self.open_url(url)

    @classmethod
    def create(
        cls,
        url: str,
        name: Optional[str] = None,
        image: Optional[str] = None,
        memory: int = 4,
        cpus: int = 2,
        disk: str = "30gb",
        reserve_ip: bool = False,
        ssh_key: Optional[str] = None,
    ) -> "WebApp":
        """
        Create a desktop VM and open a URL.

        Args:
            url: URL to open after creation.
            name: Name of the VM. Defaults to None.
            image: Image to use for the VM. Defaults to None.
            memory: Memory the VM has. Defaults to 4.
            cpus: CPUs the VM has. Defaults to 2.
            disk: Disk size for the VM. Defaults to "30gb".
            reserve_ip: Whether to reserve IP for the VM. Defaults to False.
            ssh_key: SSH key for the VM. Defaults to None.

        Returns:
            An instance of the WebApp class.
        """
        desktop = super(WebApp, cls).create(
            name=name,
            provider=QemuProvider(),
            image=image,
            memory=memory,
            cpus=cpus,
            disk=disk,
            reserve_ip=reserve_ip,
            ssh_key=ssh_key,
        )
        desktop.open_url(url)
        return desktop

    @classmethod
    def ec2(
        cls,
        url: str,
        name: Optional[str] = None,
        region: Optional[str] = None,
        image: Optional[str] = None,
        memory: int = 4,
        cpus: int = 2,
        disk: str = "30gb",
        reserve_ip: bool = False,
        ssh_key: Optional[str] = None,
    ) -> "WebApp":
        """
        Create a desktop VM on EC2 and open a URL.

        Args:
            url: URL to open after creation.
            name: Name of the VM. Defaults to None.
            region: AWS region for the VM. Defaults to None.
            image: AMI to use for the VM. Defaults to None.
            memory: Memory the VM has. Defaults to 4.
            cpus: CPUs the VM has. Defaults to 2.
            disk: Disk size for the VM. Defaults to "30gb".
            reserve_ip: Whether to reserve IP for the VM. Defaults to False.
            ssh_key: SSH key for the VM. Defaults to None.

        Returns:
            An instance of the WebApp class.
        """
        desktop = super(WebApp, cls).ec2(
            name=name,
            region=region,
            image=image,
            memory=memory,
            cpus=cpus,
            disk=disk,
            reserve_ip=reserve_ip,
            ssh_key=ssh_key,
        )
        desktop.open_url(url)
        return desktop

    @classmethod
    def gce(
        cls,
        url: str,
        name: Optional[str] = None,
        project: Optional[str] = None,
        zone: Optional[str] = None,
        image: Optional[str] = None,
        memory: int = 4,
        cpus: int = 2,
        disk: str = "30gb",
        reserve_ip: bool = False,
        ssh_key: Optional[str] = None,
    ) -> "WebApp":
        """
        Create a desktop VM on GCE and open a URL.

        Args:
            url: URL to open after creation.
            name: Name of the VM. Defaults to None.
            project: GCP project for the VM. Defaults to None.
            zone: GCP zone for the VM. Defaults to None.
            image: Image to use for the VM. Defaults to None.
            memory: Memory the VM has. Defaults to 4.
            cpus: CPUs the VM has. Defaults to 2.
            disk: Disk size for the VM. Defaults to "30gb".
            reserve_ip: Whether to reserve IP for the VM. Defaults to False.
            ssh_key: SSH key for the VM. Defaults to None.

        Returns:
            An instance of the WebApp class.
        """
        desktop = super(WebApp, cls).gce(
            name=name,
            project=project,
            zone=zone,
            image=image,
            memory=memory,
            cpus=cpus,
            disk=disk,
            reserve_ip=reserve_ip,
            ssh_key=ssh_key,
        )
        desktop.open_url(url)
        return desktop

    @classmethod
    def local(
        cls,
        url: str,
        name: Optional[str] = None,
        memory: int = 4,
        cpus: int = 2,
    ) -> "WebApp":
        """
        Create a local VM and open a URL.

        Args:
            url: URL to open after creation.
            name: Name of the VM. Defaults to None.
            memory: Memory the VM has. Defaults to 4.
            cpus: CPUs the VM has. Defaults to 2.

        Returns:
            An instance of the WebApp class.
        """
        desktop = super(WebApp, cls).local(
            name=name,
            memory=memory,
            cpus=cpus,
        )
        desktop.open_url(url)
        return desktop

    @classmethod
    def ensure(
        cls,
        url: str,
        name: str,
        provider: Optional[DesktopProvider] = QemuProvider(),
        image: Optional[str] = None,
        memory: int = 4,
        cpus: int = 2,
        disk: str = "30gb",
        reserve_ip: bool = False,
        ssh_key: Optional[str] = None,
    ) -> "WebApp":
        """
        Ensure a desktop VM exists with the given name, or create it if it does not, and open a URL.

        Args:
            url: URL to open after ensuring the VM exists.
            name: Name of the VM to find or create.
            provider: The provider for VM creation, defaults to QemuProvider if not specified.
            image: Image to use for the VM. Defaults to None.
            memory: Memory the VM has. Defaults to 4.
            cpus: CPUs the VM has. Defaults to 2.
            disk: Disk size for the VM. Defaults to "30gb".
            reserve_ip: Whether to reserve an IP for the VM. Defaults to False.
            ssh_key: SSH key for the VM. Defaults to None.

        Returns:
            An instance of the WebApp class.
        """
        desktop = super(WebApp, cls).ensure(
            name=name,
            provider=provider,
            image=image,
            memory=memory,
            cpus=cpus,
            disk=disk,
            reserve_ip=reserve_ip,
            ssh_key=ssh_key,
        )

        desktop.open_url(url)

        return desktop
