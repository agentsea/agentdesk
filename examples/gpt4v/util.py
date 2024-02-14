from PIL import Image
import base64
from io import BytesIO
from typing import Union, Tuple


def visualize_b64_img(b64_str: str) -> Union[Image.Image, None]:
    try:
        img_data = base64.b64decode(b64_str)
        img_io = BytesIO(img_data)
        img = Image.open(img_io)

        return img
    except Exception as e:
        print(f"Error loading image: {e}")
        return None


def encode_image(image_path):
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode("utf-8")


def reduce_image_resolution(b64_str: str, target_size: Tuple[int, int]) -> str:
    """
    Reduces the resolution of a base64-encoded image using the LANCZOS resampling filter.
    """
    # Decode the base64 string to bytes
    img_data: bytes = base64.b64decode(b64_str)
    img: Image.Image = Image.open(BytesIO(img_data))

    # Resize the image using LANCZOS resampling
    resized_img: Image.Image = img.resize(target_size, Image.Resampling.LANCZOS)

    # Convert the resized image back to a base64 string
    buffer: BytesIO = BytesIO()
    resized_img.save(buffer, format=img.format)
    new_b64_str: str = base64.b64encode(buffer.getvalue()).decode("utf-8")

    return new_b64_str
