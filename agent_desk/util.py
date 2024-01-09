import io
import os
from urllib.parse import urlparse
import random
import string


from google.cloud import storage
from PIL import Image


def extract_file_path(uri):
    """Extracts the file path from a URI"""
    parsed_uri = urlparse(uri)
    if parsed_uri.scheme == "file":
        # Remove the leading '/' on Windows
        if os.name == "nt" and parsed_uri.path.startswith("/"):
            return parsed_uri.path[1:]
        return parsed_uri.path
    else:
        raise ValueError("Invalid URI scheme. Only 'file://' is supported.")


def extract_gcs_info(gcs_uri):
    """
    Extracts the bucket name and object path from a GCS URI.

    Args:
        gcs_uri (str): The GCS URI (e.g., 'gs://bucket_name/path/to/object').

    Returns:
        tuple: A tuple containing the bucket name and object path.
    """
    parsed_uri = urlparse(gcs_uri)
    if parsed_uri.scheme != "gs":
        raise ValueError("Invalid URI scheme. Only 'gs://' is supported.")

    bucket_name = parsed_uri.netloc
    object_path = parsed_uri.path.lstrip("/")  # Remove leading '/' from the path

    return bucket_name, object_path


def upload_image_to_gcs(
    bucket_name: str, destination_blob_name: str, image: Image
) -> str:
    """
    Uploads a PIL image to Google Cloud Storage, makes it public, and returns the public URL.

    Args:
    bucket_name (str): Name of the GCS bucket.
    destination_blob_name (str): Destination blob name in the GCS bucket.
    image (PIL.Image): Image to upload.

    Returns:
    str: Public URL of the uploaded image.
    """
    # Initialize a storage client
    storage_client = storage.Client()

    # Get the bucket
    bucket = storage_client.bucket(bucket_name)

    # Convert PIL image to bytes
    img_byte_arr = io.BytesIO()
    image.save(img_byte_arr, format="PNG")
    img_byte_arr = img_byte_arr.getvalue()

    # Create a new blob and upload the image
    blob = bucket.blob(destination_blob_name)
    blob.upload_from_string(img_byte_arr, content_type="image/png")

    # Make the blob publicly accessible
    blob.make_public()

    print(f"File {destination_blob_name} uploaded to {bucket_name} and made public.")

    return blob.public_url


def generate_random_string(length: int = 8):
    """Generate a random string of fixed length."""
    letters = string.ascii_letters + string.digits
    return "".join(random.choices(letters, k=length))
