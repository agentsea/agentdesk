from abc import abstractmethod


class ImgProcessor:
    """Preprocess screenshots"""

    @abstractmethod
    def process_path(self, img_path: str, output_path: str) -> None:
        """Process an image at a path

        Args:
            img_path (str): Input image path
            output_path (str): Output image path
        """
        pass

    @abstractmethod
    def process_b64(self, b64_img: str) -> str:
        """Process a b64 image

        Args:
            b64_img (str): b64 input image

        Returns:
            str: Output b64 image
        """
        pass
