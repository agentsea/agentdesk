from PIL import Image
import base64
from io import BytesIO
from typing import Union


def visualize_b64_img(b64_str: str) -> Union[Image.Image, None]:
    try:
        # Decode the base64 string
        img_data = base64.b64decode(b64_str)

        # Convert binary data to a file-like object
        img_io = BytesIO(img_data)

        # Load image from the file-like object
        img = Image.open(img_io)

        return img
    except Exception as e:
        print(f"Error loading image: {e}")
        return None


def encode_image(image_path):
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode("utf-8")
