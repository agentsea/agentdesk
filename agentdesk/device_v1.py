# from __future__ import annotations
import atexit
import base64
import io
import urllib.parse
from enum import Enum
from typing import Any, List, Optional, Tuple, Type

import requests
from devicebay import Action, Device, ReactComponent, action, observation
from PIL import Image
from pydantic import BaseModel
from tenacity import retry, stop_after_attempt, wait_fixed

from agentdesk.server.models import V1DesktopInstance

from .key import SSHKeyPair
from .runtime.base import DesktopInstance
from .runtime.load import load_provider
from .server.models import V1ProviderData

try:
    from .runtime.gce import GCEProvider
except ImportError:
    print(
        "GCE provider unavailable, install with `pip install agentdesk[gce] if desired"
    )
try:
    from .runtime.ec2 import EC2Provider
except ImportError:
    print(
        "AWS provider unavailable, install with `pip install agentdesk[aws] if desired"
    )

from .proxy import cleanup_proxy, ensure_ssh_proxy
from .runtime.docker import DockerProvider
from .runtime.kube import KubeConnectConfig, KubernetesProvider
from .runtime.qemu import QemuProvider
from .util import (
    b64_to_image,
    extract_file_path,
    extract_gcs_info,
    generate_random_string,
)


class StorageStrategy(Enum):
    GCS = "gcs"
    LOCAL = "local"


class ConnectConfig(BaseModel):
    agentd_url: Optional[str] = None
    instance: Optional[V1DesktopInstance | str] = (
        None  # instance can be either a json stringified V1DesktopInstance or a searchable instance name
    )
    api_key: Optional[str] = None
    storage_uri: str = "file://.media"
    type_min_interval: float = 0.02
    type_max_interval: float = 0.5
    move_mouse_duration: float = 1.0
    mouse_tween: str = "easeInOutQuad"
    store_img: bool = False
    requires_proxy: bool = True
    proxy_type: str = "process"
    proxy_port: int = 8000
    private_ssh_key: Optional[str] = None
    ssh_port: int = 22


class ProvisionConfig(BaseModel):
    provider: V1ProviderData = V1ProviderData(type="docker")
    image: Optional[str] = None
    memory: int = 4
    cpus: int = 2
    disk: str = "30gb"
    reserve_ip: bool = False
    ssh_key_pair: Optional[str] = None
    proxy_port: int = 8000


class Desktop(Device):
    """Desktop OS as a device via agentd"""

    def __init__(
        self,
        agentd_url: Optional[str] = None,
        instance: Optional[DesktopInstance] = None,
        storage_uri: str = "file://.media",
        type_min_interval: float = 0.02,
        type_max_interval: float = 0.5,
        move_mouse_duration: float = 1.0,
        mouse_tween: str = "easeInOutQuad",
        store_img: bool = False,
        requires_proxy: bool = True,
        proxy_type: str = "process",
        proxy_port: int = 8000,
        private_ssh_key: Optional[str] = None,
        ssh_port: int = 22,
        check_health: bool = True,
        api_key: Optional[str] = None,
    ) -> None:
        """Connect to an agent desktop

        Args:
            agentd_url (str, optional): URL of a running agentd server. Defaults to None.
            instance (str, optional): Optional desktop VM to use. Defaults to None.
            storage_uri (str): The directory where to store images or videos taken of the Instance, supports gs:// or file://. Defaults to file://.media.
            type_min_interval (float, optional): Min interval between pressing next key. Defaults to 0.05.
            type_max_interval (float, optional): Max interval between pressing next key. Defaults to 0.25.
            move_mouse_duration (float, optional): How long should it take to move. Defaults to 1.0.
            mouse_tween (str, optional): The movement tween. Defaults to "easeInOutQuad".
            store_img (bool, optional): Whether to store the image in the cloud. Defaults to false
            requires_proxy (bool, optional): Whether to use a proxy. Defaults to True.
            proxy_type (str, optional): The type of proxy to use. Defaults to process.
            proxy_port (int, optional): The port to use for the proxy. Defaults to 8000.
            private_ssh_key (str, optional): The private ssh key to use for the proxy. Defaults to None.
            ssh_port (int, optional): The port to use for the ssh connection. Defaults to 22.
            check_health (bool, optional): Whether to check the health of the server. Defaults to True.
        """
        super().__init__()
        self._instance = instance
        self._agentd_url = agentd_url
        self.api_key = api_key
        self._key_pair_name = None
        if instance:
            if instance.requires_proxy:
                self._agentd_url = instance.addr
                self.base_url = f"localhost:{proxy_port}"
            else:
                self.base_url = instance.addr
                if instance.provider and instance.provider.type == "qemu":
                    self.base_url = f"{instance.addr}:8000"
                self._agentd_url = self.base_url

            if instance.key_pair_name:
                self._key_pair_name = instance.key_pair_name
                keys = SSHKeyPair.find(name=self._key_pair_name)
                if not keys:
                    raise ValueError(f"No key found with name {self._key_pair_name}")
                key_pair = keys[0]

                private_ssh_key = key_pair.decrypt_private_key(key_pair.private_key)

        else:
            self.base_url = agentd_url

        if not self.base_url.startswith("http"):  # type: ignore
            self.base_url = f"http://{self.base_url}"

        self.storage_uri = storage_uri
        self._type_min_interval = type_min_interval
        self._type_max_interval = type_max_interval
        self._move_mouse_duration = move_mouse_duration
        self._mouse_tween = mouse_tween
        self._store_img = store_img
        self._proxy_port = proxy_port
        self._requires_proxy = (
            requires_proxy if instance is None else instance.requires_proxy
        )
        self._private_ssh_key = private_ssh_key
        self._ssh_port = ssh_port
        self._proxy_type = proxy_type

        if self._requires_proxy:
            if (
                instance and instance.provider and instance.provider.type == "kube"
            ):  # TODO: use `provider.proxy` for everything
                if not instance.provider.args:
                    raise ValueError(f"No args for intance {instance.id}")

                cfg = KubeConnectConfig.model_validate_json(
                    instance.provider.args["cfg"]
                )
                provider = KubernetesProvider(cfg=cfg)

                local_port, _ = provider.proxy(
                    instance.name, container_port=instance.agentd_port
                )
                self._agentd_url = f"http://localhost:{local_port}"
                self.base_url = f"http://localhost:{local_port}"

            elif proxy_type == "process":
                print("starting proxy to instance...")
                proxy_pid = ensure_ssh_proxy(
                    local_port=proxy_port,
                    remote_port=8000,
                    ssh_port=instance.ssh_port if instance else ssh_port,
                    ssh_user="agentsea",
                    ssh_host=instance.addr if instance else agentd_url,  # type: ignore
                    ssh_key=private_ssh_key,
                )
                atexit.register(cleanup_proxy, proxy_pid)
                print(
                    f"proxy from local port {proxy_port} to remote port 8000 started..."
                )
                self.base_url = f"http://localhost:{proxy_port}"
            elif proxy_type == "mock":
                pass
        else:
            print("instance doesn't require proxy")

        try:
            if check_health:

                @retry(stop=stop_after_attempt(5), wait=wait_fixed(1))
                def connect():
                    if check_health:
                        resp = self.health()
                        if resp["status"] != "ok":
                            raise ValueError("agentd status is not ok")
                        print("connected to desktop via agentd")

                connect()

        except Exception as e:
            raise SystemError(f"could not connect to desktop, is agentd running? {e}")

    @classmethod
    def ensure(
        cls,
        name: str,
        config: ProvisionConfig,
    ) -> "Desktop":
        """Find or create a desktop"""
        instance = DesktopInstance.get(name)
        if instance:
            return cls.from_instance(instance)

        return cls.create(
            name=name,
            config=config,
        )

    @classmethod
    def create(
        cls,
        name: Optional[str] = None,
        config: ProvisionConfig = ProvisionConfig(),
    ) -> "Desktop":
        """Create a desktop VM"""

        provider = load_provider(config.provider)
        instance = provider.create(
            name=name,
            image=config.image,
            memory=config.memory,
            cpu=config.cpus,
            disk=config.disk,
            reserve_ip=config.reserve_ip,
            ssh_key_pair=config.ssh_key_pair,
        )
        return cls.from_instance(instance, proxy_port=config.proxy_port)

    def delete(self) -> None:
        """Delete the desktop VM"""
        if not self._instance:
            raise ValueError("Desktop instance not found")
        self._instance.delete()

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
        ssh_key_pair: Optional[str] = None,
    ) -> "Desktop":
        """Create a desktop VM on EC2"""
        if not region:
            region = "us-east-1"

        config = ProvisionConfig(
            provider=EC2Provider(region=region).to_data(),  # type: ignore
            image=image,
            memory=memory,
            cpus=cpus,
            disk=disk,
            reserve_ip=reserve_ip,
            ssh_key_pair=ssh_key_pair,
        )
        return cls.create(name=name, config=config)

    @classmethod
    def docker(
        cls,
        name: Optional[str] = None,
        image: Optional[str] = None,
        memory: int = 2,
        cpus: int = 1,
        disk: str = "30gb",
        reserve_ip: bool = False,
        ssh_key_pair: Optional[str] = None,
    ) -> "Desktop":
        """Create a desktop container on docker"""

        config = ProvisionConfig(
            provider=DockerProvider().to_data(),  # type: ignore
            image=image,
            memory=memory,
            cpus=cpus,
            disk=disk,
            reserve_ip=reserve_ip,
            ssh_key_pair=ssh_key_pair,
        )
        return cls.create(name=name, config=config)

    @classmethod
    def kube(
        cls,
        name: Optional[str] = None,
        image: Optional[str] = None,
        memory: int = 2,
        cpus: int = 1,
        disk: str = "30gb",
        reserve_ip: bool = False,
        ssh_key_pair: Optional[str] = None,
    ) -> "Desktop":
        """Create a desktop container on kubernetes"""
        cfg = KubeConnectConfig()

        config = ProvisionConfig(
            provider=KubernetesProvider(cfg=cfg).to_data(),  # type: ignore
            image=image,
            memory=memory,
            cpus=cpus,
            disk=disk,
            reserve_ip=reserve_ip,
            ssh_key_pair=ssh_key_pair,
        )
        return cls.create(name=name, config=config)

    @classmethod
    def gce(
        cls,
        name: Optional[str] = None,
        project: Optional[str] = None,
        zone: str = "us-central1-a",
        region: str = "us-central1",
        image: Optional[str] = None,
        memory: int = 4,
        cpus: int = 2,
        disk: str = "30gb",
        reserve_ip: bool = False,
        ssh_key_pair: Optional[str] = None,
    ) -> "Desktop":
        """Create a desktop VM on GCE"""
        config = ProvisionConfig(
            provider=GCEProvider(  # type: ignore
                project_id=project, zone=zone, region=region
            ).to_data(),  # type: ignore
            image=image,
            memory=memory,
            cpus=cpus,
            disk=disk,
            reserve_ip=reserve_ip,
            ssh_key_pair=ssh_key_pair,
        )
        return cls.create(name=name, config=config)

    @classmethod
    def local(
        cls,
        name: Optional[str] = None,
        memory: int = 4,
        cpus: int = 2,
    ) -> "Desktop":
        """Create a local VM

        Args:
            name (str, optional): Name of the instance. Defaults to None.
            memory (int, optional): Memory the VM has. Defaults to 4.
            cpus (int, optional): CPUs the VM has. Defaults to 2.

        Returns:
            Desktop: A desktop
        """
        config = ProvisionConfig(
            provider=QemuProvider().to_data(), memory=memory, cpus=cpus
        )
        return cls.create(name=name, config=config)

    @classmethod
    def connect(cls, config: ConnectConfig) -> "Desktop":
        instance = None
        if config.instance:
            if isinstance(config.instance, V1DesktopInstance):
                print("Valid instance of V1DesktopInstance", flush=True)
                instance = DesktopInstance.from_v1(config.instance)
                print("Successfully created DesktopInstance:", instance, flush=True)
            else:
                print("instance isn't a V1DesktopInstance", flush=True)
                vms = DesktopInstance.find(name=config.instance)
                if not vms:
                    raise ValueError(f"VM {config.instance} was not found")
                instance = vms[0]
        return cls(
            agentd_url=config.agentd_url,
            private_ssh_key=config.private_ssh_key,
            instance=instance,
            storage_uri=config.storage_uri,
            type_min_interval=config.type_min_interval,
            type_max_interval=config.type_max_interval,
            move_mouse_duration=config.move_mouse_duration,
            mouse_tween=config.mouse_tween,
            store_img=config.store_img,
            requires_proxy=config.requires_proxy,
            proxy_type=config.proxy_type,
            proxy_port=config.proxy_port,
            ssh_port=config.ssh_port,
            api_key=config.api_key,
        )

    def disconnect(self) -> None:
        """Disconnect from the device"""
        pass

    def connect_config(self) -> ConnectConfig:
        ssh_private_key = None
        if self._key_pair_name:
            keys = SSHKeyPair.find(name=self._key_pair_name)
            if not keys:
                raise ValueError(f"No key found with name {self._key_pair_name}")
            key_pair = keys[0]

            ssh_private_key = key_pair.decrypt_private_key(key_pair.private_key)
        instance = self._instance
        if isinstance(instance, DesktopInstance):
            instance = instance.to_v1_schema()

        requires_proxy = False if self._requires_proxy is None else self._requires_proxy

        return ConnectConfig(
            instance=instance,
            agentd_url=self._agentd_url,
            storage_uri=self.storage_uri,
            type_min_interval=self._type_min_interval,
            type_max_interval=self._type_max_interval,
            move_mouse_duration=self._move_mouse_duration,
            mouse_tween=self._mouse_tween,
            store_img=self._store_img,
            requires_proxy=requires_proxy,
            proxy_type=self._proxy_type,
            proxy_port=self._proxy_port,
            private_ssh_key=ssh_private_key,
            ssh_port=self._ssh_port,
        )

    @classmethod
    def connect_config_type(cls) -> Type[ConnectConfig]:
        return ConnectConfig

    @classmethod
    def provision_config_type(cls) -> Type[ProvisionConfig]:
        return ProvisionConfig

    @classmethod
    def react_component(cls) -> ReactComponent:
        return ReactComponent()

    @classmethod
    def from_instance(
        cls,
        instance: DesktopInstance,
        proxy_type: str = "process",
        proxy_port: int = 8000,
        check_health: bool = True,
    ) -> "Desktop":
        """Create a desktop from a VM

        Args:
            instance (DesktopInstance): Instance to use
            proxy_type (str, optional): The type of proxy to use. Defaults to process.
            proxy_port (int, optional): The port to use for the proxy. Defaults to 8000.
            check_health (bool, optional): Check the health of the VM. Defaults to True.

        Returns:
            Desktop: A desktop
        """
        return Desktop(
            instance=instance,
            proxy_type=proxy_type,
            proxy_port=proxy_port,
            check_health=check_health,
            ssh_port=instance.ssh_port,
        )

    @classmethod
    def get(cls, name: str) -> Optional[DesktopInstance]:
        """Get a desktop by name

        Args:
            name (str): Name of the desktop

        Returns:
            Desktop: A desktop
        """
        return DesktopInstance.get(name)

    @classmethod
    def find(cls, **kwargs: Any) -> list[DesktopInstance]:
        """List all desktops

        Returns:
            list[DesktopInstance]: A list of desktop vms
        """
        return DesktopInstance.find(**kwargs)

    def _get_headers(self) -> dict:
        """Helper to return headers with optional Authorization"""
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def info(self) -> dict:
        """Get info on the desktop runtime

        Returns:
            dict: A dictionary of info
        """
        response = requests.get(f"{self.base_url}/v1/info", headers=self._get_headers())
        response.raise_for_status()
        return response.json()

    def view(self, background: bool = False) -> None:
        """View the desktop

        Args:
            background (bool, optional): Whether to run in the background and not block. Defaults to False.
        """

        if not self._instance:
            raise ValueError("Desktop not created with a VM, don't know how to proxy")

        self._instance.view(background)

    def health(self) -> dict:
        """Health of agentd

        Returns:
            dict: Agentd health
        """
        url = f"{self.base_url}/health"
        print(f"checking desktop device health at url: {url}")
        response = requests.get(url, headers=self._get_headers())
        response.raise_for_status()
        return response.json()

    @action
    def open_url(self, url: str) -> None:
        """Open a URL in chromium

        Args:
            url (str): URL to open
        """
        response = requests.post(
            f"{self.base_url}/v1/open_url",
            json={"url": url},
            headers=self._get_headers(),
        )
        response.raise_for_status()
        return

    @action
    def move_mouse(self, x: int, y: int) -> None:
        """Move mouse to a position

        Args:
            x (int): x coordinate
            y (int): y coordinate
        """
        response = requests.post(
            f"{self.base_url}/v1/move_mouse",
            json={
                "x": x,
                "y": y,
                "duration": self._move_mouse_duration,
                "tween": self._mouse_tween,
            },
            headers=self._get_headers(),
        )
        response.raise_for_status()
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
            body["location"] = {"x": x, "y": y}  # type: ignore

        response = requests.post(
            f"{self.base_url}/v1/click", json=body, headers=self._get_headers()
        )
        response.raise_for_status()
        return

    @action
    def press_key(self, key: str) -> None:
        """Press a key

        Args:
            key (str): Which key to press. Options are:
                [ "\\t", "\\n", "\\r", " ", "!", '\\"', "\\#", "\\$", "\\%", "\\&", "\\'",
                "\\(", "\\)", "\\*", "\\+", ",", "-", "\\.", "/", "0", "1", "2", "3",
                "4", "5", "6", "7", "8", "9", ":", ";", "<", "=", ">", "\\?", "@",
                "\\[", "\\\\", "\\]", "\\^", "\\_", "\\`", "a", "b", "c", "d", "e",
                "f", "g", "h", "i", "j", "k", "l", "m", "n", "o", "p", "q", "r", "s",
                "t", "u", "v", "w", "x", "y", "z", "{", "|", "}", "~", "accept", "add",
                "alt", "altleft", "altright", "apps", "backspace", "browserback",
                "browserfavorites", "browserforward", "browserhome", "browserrefresh",
                "browsersearch", "browserstop", "capslock", "clear", "convert", "ctrl",
                "ctrlleft", "ctrlright", "decimal", "del", "delete", "divide", "down",
                "end", "enter", "esc", "escape", "execute", "f1", "f10", "f11", "f12",
                "f13", "f14", "f15", "f16", "f17", "f18", "f19", "f2", "f20", "f21",
                "f22", "f23", "f24", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "final",
                "fn", "help", "home", "insert", "left", "numlock", "pagedown", "pageup", "pause",
                "pgdn", "pgup", "playpause", "prevtrack", "print", "printscreen",
                "prntscrn", "prtsc", "prtscr", "return", "right", "scrolllock",
                "select", "separator", "shift", "shiftleft", "shiftright", "sleep",
                "space", "stop", "subtract", "tab", "up", "volumedown", "volumemute",
                "volumeup", "win", "winleft", "winright", "yen", "command", "option",
                "optionleft", "optionright" ]
        """
        response = requests.post(
            f"{self.base_url}/v1/press_key",
            json={"key": key},
            headers=self._get_headers(),
        )
        response.raise_for_status()
        return

    @action
    def hot_key(self, keys: List[str]) -> None:
        """Press a hot key. For example ctrl+c

        Args:
            keys (List[str]): Which keys to press. Options are:
                [ "\\t", "\\n", "\\r", " ", "!", '\\"', "\\#", "\\$", "\\%", "\\&", "\\'",
                "\\(", "\\)", "\\*", "\\+", ",", "-", "\\.", "/", "0", "1", "2", "3",
                "4", "5", "6", "7", "8", "9", ":", ";", "<", "=", ">", "\\?", "@",
                "\\[", "\\\\", "\\]", "\\^", "\\_", "\\`", "a", "b", "c", "d", "e",
                "f", "g", "h", "i", "j", "k", "l", "m", "n", "o", "p", "q", "r", "s",
                "t", "u", "v", "w", "x", "y", "z", "{", "|", "}", "~", "accept", "add",
                "alt", "altleft", "altright", "apps", "backspace", "browserback",
                "browserfavorites", "browserforward", "browserhome", "browserrefresh",
                "browsersearch", "browserstop", "capslock", "clear", "convert", "ctrl",
                "ctrlleft", "ctrlright", "decimal", "del", "delete", "divide", "down",
                "end", "enter", "esc", "escape", "execute", "f1", "f10", "f11", "f12",
                "f13", "f14", "f15", "f16", "f17", "f18", "f19", "f2", "f20", "f21",
                "f22", "f23", "f24", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "final",
                "fn", "help", "home", "insert", "left", "numlock", "pagedown", "pageup", "pause",
                "pgdn", "pgup", "playpause", "prevtrack", "print", "printscreen",
                "prntscrn", "prtsc", "prtscr", "return", "right", "scrolllock",
                "select", "separator", "shift", "shiftleft", "shiftright", "sleep",
                "space", "stop", "subtract", "tab", "up", "volumedown", "volumemute",
                "volumeup", "win", "winleft", "winright", "yen", "command", "option",
                "optionleft", "optionright" ]
        """
        response = requests.post(
            f"{self.base_url}/v1/hot_key",
            json={"keys": keys},
            headers=self._get_headers(),
        )
        response.raise_for_status()
        return

    @action
    def scroll(self, clicks: int = -3) -> None:
        """Scroll the screen

        Args:
            clicks (int, optional): Number of clicks, negative scrolls down, positive scrolls up. Defaults to -3.
        """
        response = requests.post(
            f"{self.base_url}/v1/scroll",
            json={"clicks": clicks},
            headers=self._get_headers(),
        )
        response.raise_for_status()
        return

    @action
    def drag_mouse(self, x: int, y: int) -> None:
        """Drag the mouse

        Args:
            x (int): x coordinate
            y (int): y coordinate
        """
        response = requests.post(
            f"{self.base_url}/v1/drag_mouse",
            json={"x": x, "y": y},
            headers=self._get_headers(),
        )
        response.raise_for_status()
        return

    @action
    def double_click(
        self, button: str = "left", x: Optional[int] = None, y: Optional[int] = None
    ) -> None:
        """Double click the mouse"""
        body = {"button": button}
        if x and y:
            body["location"] = {"x": x, "y": y}  # type: ignore

        response = requests.post(
            f"{self.base_url}/v1/double_click", json=body, headers=self._get_headers()
        )
        response.raise_for_status()
        return

    @action
    def type_text(self, text: str) -> None:
        """Type text

        Args:
            text (str): Text to type
        """
        response = requests.post(
            f"{self.base_url}/v1/type_text",
            json={
                "text": text,
                "min_interval": self._type_min_interval,
                "max_interval": self._type_max_interval,
            },
            headers=self._get_headers(),
        )
        response.raise_for_status()
        return

    @action
    def exec(self, cmd: str) -> dict:
        """Execute a command

        Args:
            cmd (str): Command to execute

        Returns:
            dict: Command execution result containing status, output and return code if error
        """
        response = requests.post(f"{self.base_url}/v1/exec", json={"command": cmd})
        response.raise_for_status()
        return response.json()

    @action
    def use_secret(
        self, 
        name: str, 
        field: str, 
        secret_server: str = "https://api.hub.agentlabs.xyz"
    ) -> dict:
        """Use Secret

        Args:
            name (str): the secret name (EX. uber_eats_credentials)
            field (str): the field name within the secret to use. (EX. username, password, or address)

        Returns:
            dict: Command execution result containing status, output and return code if error
        """
        response = requests.post(
            f"{self.base_url}/v1/use_secret",
            headers=self._get_headers(),
            json={
                "name": name, 
                "field": field,
                "server_address": secret_server,
                "token": self.api_key
            }
        )
        response.raise_for_status()
        jdict = response.json()

        return jdict

    @observation
    def take_screenshots(self, count: int = 1, delay: float = 0.0) -> List[Image.Image]:
        """Take screenshots

        Returns:
            List[Image.Image]: List of PIL Image objects
        """
        params = {"count": count, "delay": delay}
        encoded_params = urllib.parse.urlencode(params)
        response = requests.post(
            f"{self.base_url}/v1/screenshot?{encoded_params}",
            headers=self._get_headers(),
        )
        response.raise_for_status()
        jdict = response.json()

        images = jdict["images"]

        out = []
        for image in images:
            image_data = base64.b64decode(image)
            image_stream = io.BytesIO(image_data)
            img = Image.open(image_stream)
            out.append(img)

        return out
    
    @observation
    def get_secrets(
        self, 
        owner_id: str,
        server_address: str = "https://api.hub.agentlabs.xyz",
    ) -> List[dict]:
        """Get available secrets

        Args:
            owner_id (str): The owner id for who owns the secrets to be used can be a user id or and org id
            server_address (str): the secret server address

        Returns:
            List[{"name": str, "fields": List[str]}]: List of secrets available for use with the specific secret fields contained for use.
        """

        response = requests.post(
            f"{self.base_url}/v1/get_secrets",
            headers=self._get_headers(),
            json={
                "owner_id": owner_id, 
                "server_address": server_address,
                "token": self.api_key
            }
        )
        response.raise_for_status()
        jdict = response.json()

        return jdict

    @observation
    def mouse_coordinates(self) -> Tuple[int, int]:
        """Get the current mouse coordinates

        Returns:
            Tuple[int, int]: x, y coordinates
        """
        response = requests.get(
            f"{self.base_url}/v1/mouse_coordinates", headers=self._get_headers()
        )
        response.raise_for_status()
        jdict = response.json()

        return jdict["x"], jdict["y"]

    def close(self):
        pass

    def demostrate(
        self,
        task: str,
        owner_id: str,
        token: str,
        tracker_url: str = "https://api.hub.agentlabs.xyz",
    ) -> None:
        """Demostrate a task on the desktop

        Args:
            task (str): Task to demostrate.
            token (str): Token to use for the tracker.
            tracker_url (str): URL of the tracker
        """

        data = {
            "description": task,
            "token": token,
            "server_address": tracker_url,
            "owner_id": owner_id,
        }

        response = requests.post(
            f"{self.base_url}/v1/start_recording",
            json=data,
            headers=self._get_headers(),
        )
        response.raise_for_status()

        jdict = response.json()
        print("start recording response: ", jdict)

        task_id = jdict["task_id"]
        print("task_id: ", task_id)

        print("viewing desktop...")
        self.view(background=True)

        input("Press enter to stop recording...")
        print("stopping recording...")
        response = requests.post(
            f"{self.base_url}/v1/stop_recording", headers=self._get_headers()
        )
        response.raise_for_status()

        print("recording stopped")

        # Adding Bearer token to the request
        headers = {"Authorization": f"Bearer {token}"}
        response = requests.get(f"{tracker_url}/v1/tasks/{task_id}", headers=headers)
        response.raise_for_status()
        jdict = response.json()
        print("task status: ", jdict)


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
