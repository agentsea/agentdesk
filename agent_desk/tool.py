import base64
import io

import requests
from PIL import Image
from agent_tools import Tool, action, observation


class Desktop(Tool):
    """Desktop OS as a tool via agentd"""

    def __init__(self, agentd_url: str) -> None:
        """Connect to an agent desktop

        Args:
            agentd_url (str): URL of a running agentd server
        """
        super().__init__()
        self.base_url = agentd_url

        try:
            resp = self.health()
            if resp["status"] != "ok":
                raise ValueError("agentd status is not ok")
        except Exception as e:
            raise SystemError(f"could not connect to desktop, is agentd running? {e}")

        print("connected to desktop via agentd")

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
    def move_mouse_to(
        self, x: int, y: int, duration: float = 1.0, tween: str = "easeInOutQuad"
    ) -> None:
        """Move mouse to a position

        Args:
            x (int): x coordinate
            y (int): y coordiname
            duration (float, optional): How long should it take to move. Defaults to 1.0.
            tween (str, optional): The movement tween. Defaults to "easeInOutQuad".
        """
        requests.post(
            f"{self.base_url}/move_mouse_to",
            json={"x": x, "y": y, "duration": duration, "tween": tween},
        )
        return

    @action
    def click(self, button: str = "left") -> None:
        """Click mouse button

        Args:
            button (str, optional): Which button to click. Defaults to "left".
        """
        requests.post(f"{self.base_url}/click", json={"button": button})
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
    def type_text(
        self, text: str, min_interval: float = 0.05, max_interval: float = 0.25
    ) -> None:
        """Type text

        Args:
            text (str): Text to type
            min_interval (float, optional): Min interval between pressing next key. Defaults to 0.05.
            max_interval (float, optional): Max interval between pressing next key. Defaults to 0.25.
        """
        requests.post(
            f"{self.base_url}/type_text",
            json={
                "text": text,
                "min_interval": min_interval,
                "max_interval": max_interval,
            },
        )
        return

    @observation
    def take_screenshot(self) -> Image:
        """Take screenshot

        Returns:
            Image: The image
        """
        response = requests.post(f"{self.base_url}/screenshot")
        jdict = response.json()

        image_data = base64.b64decode(jdict["image"])
        image_stream = io.BytesIO(image_data)
        image = Image.open(image_stream)

        return image

    def close(self):
        pass
