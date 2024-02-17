import base64
from io import BytesIO

from PIL import Image, ImageDraw

from .base import ImgProcessor


class GridProcessor(ImgProcessor):
    """Preprocess screenshots by placing a grid over them"""

    def __init__(self, grid_px_size: int = 100):
        """Create a grid processos

        Args:
            grid_px_size (int, optional): Size of each grid. Defaults to 100.
        """
        self.grid_px_size = grid_px_size

    def draw_coordinates(
        self, draw: ImageDraw.ImageDraw, width: int, height: int
    ) -> None:
        """Draw coordinates at each grid intersection.

        Args:
            draw (ImageDraw.Draw): The draw object used to draw on the image.
            width (int): Width of the image.
            height (int): Height of the image.
        """
        for x in range(0, width, self.grid_px_size):
            for y in range(0, height, self.grid_px_size):
                coordinate_text = f"({x},{y})"
                # Adjust text position so it does not overlap with grid lines
                text_position = (x + 5, y + 5)
                draw.text(text_position, coordinate_text, fill="red")

    def process_path(self, img_path: str, output_path: str) -> None:
        with Image.open(img_path) as img:
            draw = ImageDraw.Draw(img)
            width, height = img.size

            for x in range(0, width, self.grid_px_size):
                draw.line((x, 0, x, height), fill="black")
            for y in range(0, height, self.grid_px_size):
                draw.line((0, y, width, y), fill="black")

            self.draw_coordinates(draw, width, height)
            img.save(output_path)

    def process_b64(self, b64_img: str) -> str:
        input_bytes = base64.b64decode(b64_img)
        img = Image.open(BytesIO(input_bytes))
        draw = ImageDraw.Draw(img)
        width, height = img.size

        for x in range(0, width, self.grid_px_size):
            draw.line((x, 0, x, height), fill="black")
        for y in range(0, height, self.grid_px_size):
            draw.line((0, y, width, y), fill="black")

        self.draw_coordinates(draw, width, height)
        buffer = BytesIO()
        img.save(buffer, format="PNG")
        output_b64 = base64.b64encode(buffer.getvalue()).decode("utf-8")

        return output_b64
