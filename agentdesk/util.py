import io
import os
from urllib.parse import urlparse
import random
import string
import subprocess
from typing import Optional
import socket
from subprocess import CalledProcessError, DEVNULL
from datetime import datetime
import hashlib
import base64

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
    bucket_name: str, destination_blob_name: str, image: Image  # type: ignore
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
    image.save(img_byte_arr, format="PNG")  # type: ignore
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


def get_docker_host() -> str:
    try:
        # Get the current Docker context
        current_context = (
            subprocess.check_output("docker context show", shell=True).decode().strip()
        )

        # Inspect the current Docker context and extract the host
        context_info = subprocess.check_output(
            f"docker context inspect {current_context}", shell=True
        ).decode()
        for line in context_info.split("\n"):
            if '"Host"' in line:
                return line.split('"')[3]
        return ""
    except subprocess.CalledProcessError as e:
        print(f"Error: {e.output.decode()}")
        return ""


def find_ssh_public_key() -> Optional[str]:
    """Try to find the SSH public key in the default location."""

    default_ssh_key_path = os.path.expanduser("~/.ssh/id_rsa.pub")
    if os.path.exists(default_ssh_key_path):
        print("using ssh key in ~/.ssh/id_rsa.pub")
        with open(default_ssh_key_path, "r") as file:
            return file.read().strip()
    return None


def check_command_availability(command: str) -> bool:
    """Check if a command is available in the system."""

    try:
        subprocess.run(
            [command, "--version"], stdout=DEVNULL, stderr=DEVNULL, check=True
        )
        return True
    except (FileNotFoundError, CalledProcessError):
        return False


def check_port_in_use(port: int) -> bool:
    """
    Check if the specified port is currently in use on the local machine.

    Args:
        port (int): The port number to check.

    Returns:
        bool: True if the port is in use, False otherwise.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("localhost", port)) == 0


def find_open_port(start_port: int = 1024, end_port: int = 65535) -> Optional[int]:
    """Finds an open port on the machine"""
    for port in range(start_port, end_port + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("", port))
                return port  # Port is open
            except socket.error:
                continue  # Port is in use, try the next one
    return None  # No open port found


def convert_unix_to_datetime(unix_timestamp: int) -> str:
    dt = datetime.utcfromtimestamp(unix_timestamp)
    friendly_format = dt.strftime("%Y-%m-%d %H:%M:%S")
    return friendly_format


def generate_short_hash(data: str) -> str:
    hash_object = hashlib.sha256(data.encode())  # You can use sha1 or sha256
    hash_digest = hash_object.digest()
    # Using urlsafe base64 encoding to get URL-friendly hash
    short_hash = base64.urlsafe_b64encode(hash_digest).decode("utf-8")
    return short_hash[:6]
