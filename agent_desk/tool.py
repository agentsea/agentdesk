import base64
import io
import subprocess
from enum import Enum
import time
import os
from typing import Tuple, Optional

import requests
from PIL import Image
from google.cloud import storage
from agent_tools import Tool, action, observation

from .util import extract_file_path, extract_gcs_info, generate_random_string


class StorageStrategy(Enum):
    GCS = "gcs"
    LOCAL = "local"


class Desktop(Tool):
    """Desktop OS as a tool via agentd"""

    def __init__(
        self,
        agentd_url: str,
        storage_uri: str = "file://.media",
        type_min_interval: float = 0.05,
        type_max_interval: float = 0.25,
        move_mouse_duration: float = 1.0,
        mouse_tween: str = "easeInOutQuad",
    ) -> None:
        """Connect to an agent desktop

        Args:
            agentd_url (str): URL of a running agentd server
            storage_uri (str): The directory where to store images or videos taken of the VM, supports gs:// or file://. Defaults to file://.media.
            type_min_interval (float, optional): Min interval between pressing next key. Defaults to 0.05.
            type_max_interval (float, optional): Max interval between pressing next key. Defaults to 0.25.
            move_mouse_duration (float, optional): How long should it take to move. Defaults to 1.0.
            mouse_tween (str, optional): The movement tween. Defaults to "easeInOutQuad".
        """
        super().__init__()
        self.base_url = agentd_url
        self.storage_uri = storage_uri
        self._type_min_interval = type_min_interval
        self._type_max_interval = type_max_interval
        self._move_mouse_duration = move_mouse_duration
        self._mouse_tween = mouse_tween

        try:
            resp = self.health()
            if resp["status"] != "ok":
                raise ValueError("agentd status is not ok")
        except Exception as e:
            raise SystemError(f"could not connect to desktop, is agentd running? {e}")

        print("connected to desktop via agentd")

    @classmethod
    def local(cls, memory: int = 4096, cpus: int = 4, sockify_port: int = 6080) -> None:
        command = f"qemu-system-x86_64 -hda ~/vms/ubuntu_2204.qcow2 -m {memory} "
        command += f"-smp {cpus} -netdev user,id=vmnet,hostfwd=tcp::6080-:{sockify_port},hostfwd=tcp::8000-:8000 "
        command += "-device e1000,netdev=vmnet -vnc :0"

        subprocess.Popen(command, shell=True)

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
            y (int): y coordiname
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
    def scroll(self, clicks: int = 3) -> None:
        """Scroll the screen

        Args:
            clicks (int, optional): Number of clicks. Defaults to 3.
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
            str: URI of the image
        """
        response = requests.post(f"{self.base_url}/screenshot")
        jdict = response.json()

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
