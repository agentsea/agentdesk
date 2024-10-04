from typing import Any, Dict, List, Optional, Type, Union, Iterator
import os
import platform
import time
import logging
import socket

import docker
from pydantic import BaseModel
import requests
from docker.api.client import APIClient
from docker.errors import NotFound, APIError
from tqdm import tqdm
from namesgenerator import get_random_name

from .base import DesktopInstance, V1ProviderData, DesktopProvider
from agentdesk.util import find_open_port


logger = logging.getLogger(__name__)


class DockerConnectConfig(BaseModel):
    timeout: Optional[int] = None


class DockerProvider(DesktopProvider):
    """A docker desktop provider"""

    def __init__(self, cfg: Optional[DockerConnectConfig] = None) -> None:
        self._configure_docker_socket()
        if not cfg:
            cfg = DockerConnectConfig()

        self._cfg = cfg
        if cfg.timeout:
            self.client = docker.from_env(timeout=cfg.timeout)
        else:
            self.client = docker.from_env()

    def _configure_docker_socket(self):
        if os.path.exists("/var/run/docker.sock"):
            docker_socket = "unix:///var/run/docker.sock"
        else:
            user = os.environ.get("USER")
            if os.path.exists(f"/Users/{user}/.docker/run/docker.sock"):
                docker_socket = f"unix:///Users/{user}/.docker/run/docker.sock"
            else:
                raise FileNotFoundError(
                    (
                        "Neither '/var/run/docker.sock' nor '/Users/<USER>/.docker/run/docker.sock' are available."
                        "Please make sure you have Docker installed and running."
                    )
                )
        os.environ["DOCKER_HOST"] = docker_socket

    def create(
        self,
        name: Optional[str] = None,
        image: Optional[str] = None,
        memory: int = 4,
        cpu: int = 2,
        disk: str = "30gb",
        tags: Optional[Dict[str, str]] = None,
        reserve_ip: bool = False,
        ssh_key_pair: Optional[str] = None,
        owner_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        generate_password: bool = False,
        sub_folder: Optional[str] = None,
    ) -> DesktopInstance:
        """Create a Desktop

        Args:
            name (str, optional): Name of the instance. Defaults to random generation.
            image (str, optional): Image of the instance. Defaults to Ubuntu Jammy.
            memory (int): Memory allotment. Defaults to 4gb.
            cpu (int): CPU allotment. Defaults to 2.
            disk (str): Disk allotment. Defaults to 30gb.
            tags (List[str], optional): Tags to apply to the instance. Defaults to None.
            reserve_ip (bool, optional): Reserve an IP address. Defaults to False.
            ssh_key_pair (str, optional): SSH key pair name to use. Defaults to None.
            owner_id (str, optional): Owner of the instance. Defaults to None.
            metadata (Dict[str, Any], optional): Metadata to apply to the intance. Defaults to None.
            generate_password (bool, optional): Generate a password for the instance. Defaults to False.
            sub_folder (str, optional): Subfolder to use. Defaults to None.

        Returns:
            DesktopInstance: A desktop instance
        """

        if reserve_ip:
            raise NotImplementedError(
                "Reserving IP addresses is not supported for Docker"
            )
        if ssh_key_pair:
            raise NotImplementedError("SSH key pairs are not supported for Docker")
        if generate_password:
            raise NotImplementedError(
                "Generating passwords is not supported for Docker"
            )

        if not name:
            name = get_random_name("-")
            if not name:
                raise ValueError("Could not generate a random name")

        labels = {
            "provisioner": "agentdesk",
            "desktop_name": name,
        }

        if not image:
            image = "us-docker.pkg.dev/agentsea-dev/agentd/desktop-webtop:latest"

        agentd_port = find_open_port(8000, 9000)
        if not agentd_port:
            raise ValueError("Could not find open port")

        vnc_port = find_open_port(3000, 4000)
        if not vnc_port:
            raise ValueError("Could not find open port")

        vnc_port_https = find_open_port(3100, 4100)
        if not vnc_port_https:
            raise ValueError("Could not find open port")

        env_vars = {}
        if sub_folder:
            env_vars["SUBFOLDER"] = sub_folder

        # Initialize tqdm progress bar
        api_client = APIClient()

        # Pull the image with progress tracking
        pull_image(image, api_client)

        print(f"running image {image}")
        self.ensure_network("agentsea")

        container_params = {
            "image": image,
            "network": "agentsea",
            "ports": {
                8000: agentd_port,
                3000: vnc_port,
                3001: vnc_port_https,
            },
            "environment": env_vars,
            "detach": True,
            "labels": labels,
            "name": name,
        }

        # Add extra_hosts only for Linux
        if platform.system() == "Linux":
            container_params["extra_hosts"] = {"host.docker.internal": "host-gateway"}

        try:
            container = self.client.containers.run(**container_params)
        except Exception as e:
            raise RuntimeError(f"Could not run docker desktop container '{name}': {e}")
        if container and type(container) != bytes:
            print(f"container id '{container.id}'")  # type: ignore

        # Wait for the container to be in the "running" state
        for _ in range(10):
            container.reload()  # type: ignore
            if container.status == "running":  # type: ignore
                break
            time.sleep(1)
        else:
            raise RuntimeError(f"Container '{name}' did not start in time")

        # Check /health endpoint
        health_url = f"http://localhost:{agentd_port}/health"
        for _ in range(60):
            try:
                print("waiting for agent to be ready...")
                print(f"checking health at {health_url}")
                response = requests.get(health_url)
                print(f"response: {response}")
                if response.status_code == 200:
                    print(f"Health check passed for '{name}'")
                    break
            except requests.RequestException as e:
                logger.debug(f"Health check failed: {e}")
            time.sleep(1)
        else:
            container.remove(force=True)  # type: ignore
            raise RuntimeError(f"Container '{name}' did not pass health check")

        return DesktopInstance(
            name=name,
            addr=f"http://localhost:{agentd_port}",
            cpu=cpu,
            memory=memory,
            disk=disk,
            owner_id=owner_id,
            metadata=metadata,
            agentd_port=agentd_port,
            vnc_port=vnc_port,
            vnc_port_https=vnc_port_https,
            requires_proxy=False,
            provider=self.to_data(),
        )

    def delete(self, name: str, owner_id: Optional[str] = None) -> None:
        """Delete a VM

        Args:
            name (str): Name of the VM
            owner_id (str, optional): Owner of the VM. Defaults to None
        """
        try:
            # Attempt to get the container by name
            container = self.client.containers.get(name)

            # If found, remove the container
            container.remove(force=True)  # type: ignore
            print(f"Successfully deleted container: {name}")
        except NotFound:
            # Handle the case where the container does not exist
            print(f"Container '{name}' does not exist.")
            raise
        except Exception as e:
            # Handle other potential errors
            print(f"Failed to delete container '{name}': {e}")
            raise

    def start(
        self,
        name: str,
        private_ssh_key: Optional[str] = None,
        owner_id: Optional[str] = None,
    ) -> None:
        """Start a VM

        Args:
            name (str): Name of the VM
            private_ssh_key (str, optional): SSH key to use. Defaults to use ~/.ssh/id_rsa.
        """
        raise NotImplementedError("Not implemented for container runtimes")

    def stop(self, name: str, owner_id: Optional[str] = None) -> None:
        """Stop a VM

        Args:
            name (str): Name of the VM
            owner_id (str, optional): Owner of the VM. Defaults to None
        """
        raise NotImplementedError("Not implemented for container runtimes")

    def list(self) -> List[DesktopInstance]:
        """List VMs

        Returns:
            List[VM]: A list of VMs
        """
        instances = DesktopInstance.find()

        out = []

        for instance in instances:
            if not instance.provider:
                continue

            if instance.provider.type == "docker":
                out.append(instance)

        return out

    def get(
        self, name: str, owner_id: Optional[str] = None
    ) -> Optional[DesktopInstance]:
        """Get a VM

        Args:
            name (str): Name of the VM
            owner_id (str, optional): Owner of the VM. Defaults to None
        """
        instances = DesktopInstance.find(name=name, owner_id=owner_id)
        if len(instances) == 0:
            return None
        return instances[0]

    def to_data(self) -> V1ProviderData:
        """Convert to a ProviderData object

        Returns:
            ProviderData: ProviderData object
        """
        args = {}
        if self._cfg:
            args["cfg"] = self._cfg.model_dump_json()

        return V1ProviderData(
            type="docker",
            args=args,
        )

    @classmethod
    def from_data(cls, data: V1ProviderData) -> "DockerProvider":
        """From provider data

        Args:
            data (ProviderData): Provider data
        """
        if data.args:
            cfg = DockerConnectConfig.model_validate_json(data.args["cfg"])
        else:
            cfg = DockerConnectConfig()
        return cls(cfg=cfg)

    def refresh(self, log: bool = True) -> None:
        """Refresh state"""
        label_filter = {"label": "provisioner=agentdesk"}
        running_containers = self.client.containers.list(filters=label_filter)

        # Fetch the agent instances from the database
        db_instances = self.list()

        # Create a mapping of container names to containers
        running_containers_map = {
            container.name: container for container in running_containers
        }  # type: ignore

        # Create a mapping of instance names to instances
        db_instances_map = {instance.name: instance for instance in db_instances}

        # Check for instances in the database that are not running as containers
        for instance_name, instance in db_instances_map.items():
            if instance_name not in running_containers_map:
                print(
                    f"Instance '{instance_name}' is in the database but not running. Removing from database."
                )
                instance.delete(force=True)

        logger.debug(
            "Refresh complete. State synchronized between Docker and the database."
        )

    @classmethod
    def name(cls) -> str:
        return "docker"

    @classmethod
    def connect_config_type(cls) -> Type[DockerConnectConfig]:
        return DockerConnectConfig

    def connect_config(self) -> DockerConnectConfig:
        return self._cfg

    @classmethod
    def connect(cls, cfg: DockerConnectConfig) -> "DockerProvider":
        return cls(cfg)

    def ensure_network(self, network_name: str) -> None:
        """Ensure that the specified Docker network exists, creating it if necessary."""
        try:
            self.client.networks.get(network_name)
            print(f"Network '{network_name}' already exists.")
        except NotFound:
            self.client.networks.create(network_name)
            print(f"Network '{network_name}' created.")

    def _get_host_ip(self) -> str:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # Doesn't even have to be reachable
            s.connect(("10.254.254.254", 1))
            ip = s.getsockname()[0]
        except Exception:
            ip = "127.0.0.1"
        finally:
            s.close()
        return ip

    def requires_proxy(self) -> bool:
        """Whether this runtime requires a proxy to be used"""
        return False

    def clean(self, owner_id: Optional[str] = None) -> None:
        # Define the filter for containers with the specific label
        label_filter = {"label": ["provisioner=agentdesk"]}

        # Use the filter to list containers
        containers = self.client.containers.list(filters=label_filter, all=True)

        # Initialize a list to keep track of deleted container names or IDs
        deleted_containers = []

        for container in containers:
            try:
                container_name_or_id = (
                    container.name  # type: ignore
                )  # or container.id for container ID
                container.remove(force=True)  # type: ignore
                print(f"Deleted container: {container_name_or_id}")
                deleted_containers.append(container_name_or_id)
            except Exception as e:
                print(f"Failed to delete container: {e}")

        return None

    def logs(
        self, name: str, follow: bool = False, owner_id: Optional[str] = None
    ) -> Union[str, Iterator[str]]:
        """
        Fetches the logs from the specified container. Can return all logs as a single string,
        or stream the logs as a generator of strings.

        Parameters:
            name (str): The name of the container.
            follow (bool): Whether to continuously follow the logs.

        Returns:
            Union[str, Iterator[str]]: All logs as a single string, or a generator that yields log lines.
        """
        try:
            container = self.client.containers.get(name)
            if follow:
                log_stream = container.logs(stream=True, follow=True)  # type: ignore
                return (line.decode("utf-8").strip() for line in log_stream)  # type: ignore
            else:
                return container.logs().decode("utf-8")  # type: ignore
        except NotFound:
            print(f"Container '{name}' does not exist.")
            raise
        except Exception as e:
            print(f"Failed to fetch logs for container '{name}': {e}")
            raise


def pull_image(img: str, api_client: APIClient):
    """
    Pulls a Docker image with progress bars for each layer.

    Args:
        img (str): The Docker image to pull.
        api_client (APIClient): The Docker API client.
    """

    print(f"Pulling Docker image '{img}'...")

    progress_bars = {}
    layers = {}

    try:
        for line in api_client.pull(img, stream=True, decode=True):
            if "id" in line and "progressDetail" in line:
                layer_id = line["id"]
                progress_detail = line["progressDetail"]
                current = progress_detail.get("current", 0)
                total = progress_detail.get("total", 0)

                if total:
                    if layer_id not in layers:
                        progress_bars[layer_id] = tqdm(
                            total=total,
                            desc=f"Layer {layer_id}",
                            leave=False,
                            ncols=100,
                        )
                        layers[layer_id] = 0

                    layers[layer_id] = current
                    progress_bars[layer_id].n = current
                    progress_bars[layer_id].refresh()
            elif "status" in line and "id" in line:
                print(f"Status update for {line['id']}: {line['status']}")
            elif "error" in line:
                raise APIError(line["error"])

    except APIError as e:
        print(f"Error pulling Docker image: {e.explanation}")
    except Exception as e:
        print(f"An unexpected error occurred: {str(e)}")
    finally:
        # Close all progress bars
        for bar in progress_bars.values():
            bar.n = bar.total  # Ensure the progress bar is full before closing
            bar.refresh()
            bar.close()

        print("")
