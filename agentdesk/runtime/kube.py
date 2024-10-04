from typing import Optional, Dict, Any, TypeVar, List, Iterator, Tuple, Union, Type
import atexit
import base64
import json
import logging
import os
import signal
import socket
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import random
import string

from agentdesk.util import find_open_port
from google.auth.transport.requests import Request
from google.cloud import container_v1
from google.oauth2 import service_account
from kubernetes import client, config
from kubernetes.client import Configuration
from kubernetes.client.api import core_v1_api
from kubernetes.client.rest import ApiException
from kubernetes.stream import portforward
from namesgenerator import get_random_name
from pydantic import BaseModel
from tenacity import retry, stop_after_attempt, wait_fixed
import shortuuid

from .base import DesktopInstance, DesktopProvider, V1ProviderData

logger = logging.getLogger(__name__)


class GKEOpts(BaseModel):
    cluster_name: str
    region: str
    service_account_json: str


class LocalOpts(BaseModel):
    path: Optional[str] = os.getenv("KUBECONFIG", os.path.expanduser("~/.kube/config"))


class KubeConnectConfig(BaseModel):
    provider: str = "local"
    namespace: str = "default"
    gke_opts: Optional[GKEOpts] = None
    local_opts: Optional[LocalOpts] = None


DP = TypeVar("DP", bound="KubernetesProvider")


class KubernetesProvider(DesktopProvider):
    """A provider of desktop virtual machines"""

    def __init__(self, cfg: Optional[KubeConnectConfig] = None) -> None:
        # Load the Kubernetes configuration, typically from ~/.kube/config
        if not cfg:
            cfg = KubeConnectConfig()
        self.cfg = cfg
        if cfg.provider == "gke":
            opts = cfg.gke_opts
            if not opts:
                raise ValueError("GKE opts missing")
            self.connect_to_gke(opts)
        elif cfg.provider == "local":
            opts = cfg.local_opts
            if not opts:
                opts = LocalOpts()
            if opts.path:
                config.load_kube_config(opts.path)
        else:
            raise ValueError("Unsupported provider: " + cfg.provider)

        self.core_api = core_v1_api.CoreV1Api()
        self.namespace = cfg.namespace
        self.subprocesses = []
        self.setup_signal_handlers()

    def create(
        self,
        name: Optional[str] = None,
        image: Optional[str] = None,
        memory: int = 2,
        cpu: int = 2,
        disk: str = "30gb",
        tags: Optional[Dict[str, str]] = None,
        reserve_ip: bool = False,
        ssh_key_pair: Optional[str] = None,
        owner_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        generate_password: bool = False,
        sub_folder: Optional[str] = None,
        id: Optional[str] = None,
    ) -> DesktopInstance:
        """Create a Desktop

        Args:
            name (str, optional): Name of the desktop. Defaults to random generation.
            image (str, optional): Image of the desktop. Defaults to Ubuntu Jammy.
            memory (int): Memory allotment. Defaults to 4gb.
            cpu (int): CPU allotment. Defaults to 2.
            disk (str): Disk allotment. Defaults to 30gb.
            tags (List[str], optional): Tags to apply to the desktop. Defaults to None.
            reserve_ip (bool, optional): Reserve an IP address. Defaults to False.
            ssh_key_pair (str, optional): SSH key pair name to use. Defaults to None.
            owner_id (str, optional): Owner of the desktop. Defaults to None.
            metadata (Dict[str, Any], optional): Metadata to apply to the instance. Defaults to None.
            generate_password (bool, optional): Generate a random password. Defaults to False.
            sub_folder (str, optional): Subfolder to use. Defaults to None.
            id (str, optional): ID of the desktop. Defaults to None.

        Returns:
            DesktopInstance: An instance
        """
        if reserve_ip:
            raise NotImplementedError("Reserving IP addresses is not supported yet")
        if ssh_key_pair:
            raise NotImplementedError("SSH key pairs are not supported yet")

        if not name:
            name = get_random_name("-")
            if not name:
                raise ValueError("Could not generate a random name")

        env_vars = {}

        if not id:
            id = shortuuid.uuid()

        basic_auth_password = None
        basic_auth_user = None
        if generate_password:
            # generate a random 24 character password
            basic_auth_password = "".join(
                random.choice(string.ascii_letters + string.digits) for _ in range(24)
            )

            env_vars["CUSTOM_USER"] = id
            env_vars["PASSWORD"] = basic_auth_password

        if sub_folder:
            env_vars["SUBFOLDER"] = sub_folder

        if not image:
            image = "us-docker.pkg.dev/agentsea-dev/agentd/desktop-webtop:latest"

        secret = None
        if env_vars:
            # Create a secret for the environment variables
            print("creating secret...")
            secret: Optional[client.V1Secret] = self.create_secret(name, env_vars)
            env_from = [
                client.V1EnvFromSource(
                    secret_ref=client.V1SecretEnvSource(name=secret.metadata.name)  # type: ignore
                )
            ]
        else:
            env_from = []

        # Resource configurations as before
        resources = client.V1ResourceRequirements(
            requests={"memory": f"{memory}Gi", "cpu": cpu},
            limits={"memory": "4Gi", "cpu": "4"},
        )

        logger.debug("using resources: ", resources.__dict__)

        pod_name = self._get_pod_name(name)

        # Container configuration
        container = client.V1Container(
            name=name,
            image=image,
            ports=[
                client.V1ContainerPort(container_port=8000),
                client.V1ContainerPort(container_port=3000),
                client.V1ContainerPort(container_port=3001),
            ],
            resources=resources,
            env_from=env_from,  # Using envFrom to source env vars from the secret
            image_pull_policy="Always",
        )

        # Pod specification
        pod_spec = client.V1PodSpec(
            containers=[container],
            restart_policy="Never",
        )

        # Pod creation
        pod = client.V1Pod(
            api_version="v1",
            kind="Pod",
            metadata=client.V1ObjectMeta(
                name=pod_name,
                labels={"provisioner": "agentdesk"},
                annotations={
                    "owner": owner_id,
                    "desktop_name": name,
                },
            ),
            spec=pod_spec,
        )

        try:
            created_pod: client.V1Pod = self.core_api.create_namespaced_pod(  # type: ignore
                namespace=self.namespace, body=pod
            )
            print(f"Pod created with name '{pod_name}'")
            # print("created pod: ", created_pod.__dict__)
            # Update secret's owner reference UID to newly created pod's UID
            if secret:
                print("updating secret refs...")
                if not secret.metadata:
                    raise ValueError("expected secret metadata to be set")
                if not created_pod.metadata:
                    raise ValueError("expected pod metadata to be set")
                secret.metadata.owner_references = [
                    client.V1OwnerReference(
                        api_version="v1",
                        kind="Pod",
                        name=pod_name,
                        uid=created_pod.metadata.uid,  # This should be set dynamically after pod creation
                    )
                ]
                self.core_api.patch_namespaced_secret(
                    name=secret.metadata.name,
                    namespace=self.namespace,
                    body=secret,  # type: ignore
                )
                print("secret refs updated")
        except ApiException as e:
            print(f"Exception when creating pod: {e}")
            raise

        self.wait_pod_ready(name)
        self.wait_for_http_200(name)

        instance = DesktopInstance(
            id=id,
            name=name,
            cpu=cpu,
            memory=memory,
            disk=disk,
            metadata=metadata,
            owner_id=owner_id,
            provider=self.to_data(),
            vnc_port=3000,
            vnc_port_https=3001,
            agentd_port=8000,
            requires_proxy=True,
            resource_name=pod_name,
            namespace=self.namespace,
            basic_auth_user=basic_auth_user,
            basic_auth_password=basic_auth_password,
        )

        return instance

    def delete(self, name: str, owner_id: Optional[str] = None) -> None:
        """Delete a desktop

        Args:
            name (str): Name of the desktop
            owner_id (str, optional): Owner of the desktop. Defaults to None
        """
        try:
            pod_name = self._get_pod_name(name)
            # Delete the pod
            self.core_api.delete_namespaced_pod(
                name=pod_name,
                namespace=self.namespace,
                body=client.V1DeleteOptions(grace_period_seconds=5),
            )
            print(f"Successfully deleted pod: {pod_name}")
        except ApiException as e:
            print(f"Failed to delete pod '{pod_name}': {e}")
            raise

        # Attempt to delete the secret
        try:
            self.core_api.delete_namespaced_secret(
                name=pod_name, namespace=self.namespace
            )
            print(f"Successfully deleted secret: {pod_name}")
        except ApiException as e:
            if e.status == 404:
                print(f"Secret '{pod_name}' not found, skipping deletion.")
            else:
                print(f"Failed to delete secret '{pod_name}': {e}")
                raise

    def start(
        self,
        name: str,
        private_ssh_key: Optional[str] = None,
        owner_id: Optional[str] = None,
    ) -> None:
        """Start a desktop

        Args:
            name (str): Name of the desktop
            private_ssh_key (str, optional): SSH key to use. Defaults to use ~/.ssh/id_rsa.
        """
        raise NotImplementedError("start not implemented")

    def stop(self, name: str, owner_id: Optional[str] = None) -> None:
        """Stop a desktop

        Args:
            name (str): Name of the desktop
            owner_id (str, optional): Owner of the desktop. Defaults to None
        """
        raise NotImplementedError("stop not implemented")

    def list(self) -> List[DesktopInstance]:
        """List desktops

        Returns:
            List[DesktopInstance]: A list of desktops
        """
        desktops = DesktopInstance.find()

        out = []
        for desktop in desktops:
            if not desktop.provider:
                continue
            if desktop.provider.type == "kube":
                out.append(desktop)

        return out

    def get(
        self, name: str, owner_id: Optional[str] = None
    ) -> Optional[DesktopInstance]:
        """Get a desktop

        Args:
            name (str): Name of the desktop
            owner_id (str, optional): Owner of the desktop. Defaults to None
        """
        desktops = DesktopInstance.find(name=name, owner_id=owner_id)

        for desktop in desktops:
            if not desktop.provider:
                continue
            if desktop.provider.type == "kube":
                return desktop

        return None

    def to_data(self) -> V1ProviderData:
        """Convert to a ProviderData object

        Returns:
            ProviderData: ProviderData object
        """
        return V1ProviderData(
            type="kube",
            args={"cfg": self.cfg.model_dump_json()},
        )

    @classmethod
    def from_data(cls, data: V1ProviderData) -> "KubernetesProvider":
        """From provider data

        Args:
            data (ProviderData): Provider data
        """
        config = None
        if data.args:
            config = KubeConnectConfig.model_validate_json(data.args["cfg"])

        return cls(cfg=config)

    def refresh(self, log: bool = True) -> None:
        """Refresh state"""

        label_selector = "provisioner=agentdesk"
        running_pods = self.core_api.list_namespaced_pod(
            namespace=self.namespace, label_selector=label_selector
        ).items

        # Fetch the agent instances from the database
        db_instances = self.list()

        # Create a mapping of pod names to pods
        running_pods_map = {pod.metadata.name: pod for pod in running_pods}  # type: ignore

        # Create a mapping of instance names to instances
        db_instances_map = {instance.name: instance for instance in db_instances}

        # Check for instances in the database that are not running as pods
        for instance_name, instance in db_instances_map.items():
            if self._get_pod_name(instance_name) not in running_pods_map:
                print(
                    f"Instance '{instance_name}' is in the database but not running. Removing from database."
                )
                instance.delete(force=True)

        logger.debug(
            "Refresh complete. State synchronized between Kubernetes and the database."
        )

    @classmethod
    def connect_config_type(cls) -> Type[KubeConnectConfig]:
        return KubeConnectConfig

    def connect_config(self) -> KubeConnectConfig:
        return self.cfg

    @classmethod
    def connect(cls, cfg: KubeConnectConfig) -> "KubernetesProvider":
        return cls(cfg)

    @retry(stop=stop_after_attempt(15))
    def connect_to_gke(self, opts: GKEOpts) -> Tuple[client.CoreV1Api, str, str]:
        """
        Sets up and returns a configured Kubernetes client (CoreV1Api) and cluster details.

        Returns:
            Tuple containing the Kubernetes CoreV1Api client object, the project ID, and the cluster name.
        """
        service_account_info = json.loads(opts.service_account_json)
        credentials = service_account.Credentials.from_service_account_info(
            service_account_info,
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )

        # Setup GKE client to get cluster information
        gke_service = container_v1.ClusterManagerClient(credentials=credentials)
        project_id = service_account_info.get("project_id")
        if not project_id or not opts.cluster_name or not opts.region:
            raise ValueError(
                "Missing project_id, cluster_name, or region in credentials or metadata"
            )

        logger.debug("K8s getting cluster...")
        cluster_request = container_v1.GetClusterRequest(
            name=f"projects/{project_id}/locations/{opts.region}/clusters/{opts.cluster_name}"
        )
        cluster = gke_service.get_cluster(request=cluster_request)

        # Configure Kubernetes client
        logger.debug("K8s getting token...")
        ca_cert = base64.b64decode(cluster.master_auth.cluster_ca_certificate)
        try:
            logger.debug("K8s refreshing token...")
            credentials.refresh(Request())
        except Exception as e:
            logger.error("K8s token refresh failed: ", e)
            raise e
        access_token = credentials.token
        logger.debug("K8s got token: ", access_token)

        cluster_name = opts.cluster_name

        kubeconfig = {
            "apiVersion": "v1",
            "kind": "Config",
            "clusters": [
                {
                    "name": cluster_name,
                    "cluster": {
                        "server": f"https://{cluster.endpoint}",
                        "certificate-authority-data": base64.b64encode(
                            ca_cert
                        ).decode(),
                    },
                }
            ],
            "contexts": [
                {
                    "name": cluster_name,
                    "context": {
                        "cluster": cluster_name,
                        "user": cluster_name,
                    },
                }
            ],
            "current-context": cluster_name,
            "users": [
                {
                    "name": cluster_name,
                    "user": {
                        "token": access_token,
                    },
                }
            ],
        }

        config.load_kube_config_from_dict(config_dict=kubeconfig)
        v1_client = client.CoreV1Api()
        logger.debug("K8s returning client...")

        return v1_client, project_id, cluster_name

    @retry(stop=stop_after_attempt(200), wait=wait_fixed(2))
    def wait_for_http_200(self, name: str, path: str = "/", port: int = 8000):
        """
        Waits for an HTTP 200 response from the specified path on the given pod.

        Parameters:
            name (str): The name of the pod.
            path (str): The path to query. Defaults to root '/'.
            port (int): The port on which the pod service is exposed. Defaults to 8000.

        Raises:
            RuntimeError: If the response is not 200 after the specified retries.
        """
        pod_name = self._get_pod_name(name)
        logger.debug(
            f"Checking HTTP 200 readiness for pod {pod_name} on path {path} and port: {port}"
        )
        print(f"Waiting for desktop {name} to be ready...")
        status_code, response_text = self.call(
            name=name, path=path, method="GET", port=port
        )
        if status_code != 200:
            logger.debug(f"Received status code {status_code}, retrying...")
            raise Exception(
                f"Pod {pod_name} at path {path} is not ready. Status code: {status_code}"
            )
        logger.debug(f"Pod {pod_name} at path {path} responded with: {response_text}")
        logger.debug(f"Pod {pod_name} at path {path} is ready with status 200.")
        print(f"Health check passed for desktop '{name}'")

    @retry(stop=stop_after_attempt(200), wait=wait_fixed(2))
    def wait_pod_ready(self, name: str) -> bool:
        """
        Checks if the specified pod is ready to serve requests.

        Parameters:
            name (str): The name of the pod to check.

        Returns:
            bool: True if the pod is ready, False otherwise.
        """
        try:
            pod_name = self._get_pod_name(name)
            pod = self.core_api.read_namespaced_pod(
                name=pod_name, namespace=self.namespace
            )
            conditions = pod.status.conditions  # type: ignore
            if conditions:
                for condition in conditions:
                    if condition.type == "Ready" and condition.status == "True":
                        return True
            print("Waiting for pod to be ready...")
            raise Exception(f"Pod {pod_name} is not ready")
        except ApiException as e:
            print(f"Failed to read pod status for '{pod_name}': {e}")
            raise

    @retry(stop=stop_after_attempt(15))
    def call(
        self,
        name: str,
        path: str,
        method: str,
        port: int = 8000,
        data: Optional[dict] = None,
        headers: Optional[dict] = None,
    ) -> Tuple[int, str]:
        c = Configuration.get_default_copy()
        c.assert_hostname = False  # type: ignore
        Configuration.set_default(c)
        core_v1 = client.CoreV1Api()
        ##############################################################################
        # Kubernetes pod port forwarding works by directly providing a socket which
        # the python application uses to send and receive data on. This is in contrast
        # to the go client, which opens a local port that the go application then has
        # to open to get a socket to transmit data.
        #
        # This simplifies the python application, there is not a local port to worry
        # about if that port number is available. Nor does the python application have
        # to then deal with opening this local port. The socket used to transmit data
        # is immediately provided to the python application.
        #
        # Below also is an example of monkey patching the socket.create_connection
        # function so that DNS names of the following formats will access kubernetes
        # ports:
        #
        #    <pod-name>.<namespace>.kubernetes
        #    <pod-name>.pod.<namespace>.kubernetes
        #    <service-name>.svc.<namespace>.kubernetes
        #    <service-name>.service.<namespace>.kubernetes
        #
        # These DNS name can be used to interact with pod ports using python libraries,
        # such as urllib.request and http.client. For example:
        #
        # response = urllib.request.urlopen(
        #     'https://metrics-server.service.kube-system.kubernetes/'
        # )
        #
        ##############################################################################

        # Monkey patch socket.create_connection which is used by http.client and
        # urllib.request. The same can be done with urllib3.util.connection.create_connection
        # if the "requests" package is used.
        socket_create_connection = socket.create_connection

        def kubernetes_create_connection(address, *args, **kwargs):
            dns_name = address[0]
            if isinstance(dns_name, bytes):
                dns_name = dns_name.decode()
            dns_name = dns_name.split(".")
            if dns_name[-1] != "kubernetes":
                return socket_create_connection(address, *args, **kwargs)
            if len(dns_name) not in (3, 4):
                raise RuntimeError("Unexpected kubernetes DNS name.")
            namespace = dns_name[-2]
            name = dns_name[0]
            port = address[1]
            # print("connecting to: ", namespace, name, port)
            if len(dns_name) == 4:
                if dns_name[1] in ("svc", "service"):
                    service = core_v1.read_namespaced_service(name, namespace)
                    for service_port in service.spec.ports:  # type: ignore
                        if service_port.port == port:
                            port = service_port.target_port
                            break
                    else:
                        raise RuntimeError(f"Unable to find service port: {port}")
                    label_selector = []
                    for key, value in service.spec.selector.items():  # type: ignore
                        label_selector.append(f"{key}={value}")
                    pods = core_v1.list_namespaced_pod(
                        namespace, label_selector=",".join(label_selector)
                    )
                    if not pods.items:
                        raise RuntimeError("Unable to find service pods.")
                    name = pods.items[0].metadata.name
                    if isinstance(port, str):
                        for container in pods.items[0].spec.containers:
                            for container_port in container.ports:
                                if container_port.name == port:
                                    port = container_port.container_port
                                    break
                            else:
                                continue
                            break
                        else:
                            raise RuntimeError(
                                f"Unable to find service port name: {port}"
                            )
                elif dns_name[1] != "pod":
                    raise RuntimeError(f"Unsupported resource type: {dns_name[1]}")
            pf = portforward(
                core_v1.connect_get_namespaced_pod_portforward,
                name,
                namespace,
                ports=str(port),
            )
            return pf.socket(port)

        socket.create_connection = kubernetes_create_connection

        namespace = self.namespace
        if not namespace:
            raise ValueError("NAMESPACE environment variable not set")
        # Access the nginx http server using the
        # "<pod-name>.pod.<namespace>.kubernetes" dns name.
        # Construct the URL with the custom path
        url = f"http://{self._get_pod_name(name).lower()}.pod.{namespace}.kubernetes:{port}{path}"

        # Create a request object based on the HTTP method
        if method.upper() == "GET":
            if data:
                # Convert data to URL-encoded query parameters for GET requests
                query_params = urllib.parse.urlencode(data)
                url += f"?{query_params}"
            request = urllib.request.Request(url)
        else:
            # Set the request method and data for POST, PUT, etc.
            request = urllib.request.Request(url, method=method.upper())
            if data:
                # Convert data to JSON string and set the request body
                request.add_header("Content-Type", "application/json")
                if headers:
                    for k, v in headers.items():
                        request.add_header(k, v)
                request.data = json.dumps(data).encode("utf-8")
            logger.debug(f"Request Data: {request.data}")

        # Send the request and handle the response
        try:
            response = urllib.request.urlopen(request)
            status_code = response.code
            response_text = response.read().decode("utf-8")
            logger.debug(f"Status Code: {status_code}")

            # Parse the JSON response and return a dictionary
            return status_code, response_text
        except urllib.error.HTTPError as e:
            status_code = e.code
            error_message = e.read().decode("utf-8")
            logger.error(f"Error: {status_code}")
            logger.error(error_message)

            raise SystemError(
                f"Error making http request kubernetes pod {status_code}: {error_message}"
            )
        finally:
            try:
                if response:  # type: ignore
                    response.close()
            except Exception:
                pass

    def setup_signal_handlers(self):
        signal.signal(signal.SIGINT, self.graceful_exit)
        signal.signal(signal.SIGTERM, self.graceful_exit)
        atexit.register(self.cleanup_subprocesses)

    def _register_cleanup(self, proc: subprocess.Popen):
        self.subprocesses.append(proc)

    def cleanup_subprocesses(self):
        for proc in self.subprocesses:
            if proc.poll() is None:  # Process is still running
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
        self.subprocesses = []  # Clear the list after cleaning up

    def graceful_exit(self, signum, frame):
        self.cleanup_subprocesses()
        sys.exit(signum)  # Exit with the signal number

    def requires_proxy(self) -> bool:
        """Whether this runtime requires a proxy to be used"""
        return True

    def _get_pod_name(self, name: str) -> str:
        """Get the pod name for the given name"""
        return f"desk-{name}"

    def proxy(
        self,
        name: str,
        local_port: Optional[int] = None,
        container_port: int = 3000,
        background: bool = True,
        owner_id: Optional[str] = None,
    ) -> Tuple[int, Optional[int]]:
        """Proxy the desktop port to localhost.

        Args:
            name (str): Name of the agent
            local_port (Optional[int], optional): Local port to proxy to. Defaults to None.
            container_port (int, optional): Container port. Defaults to 3000.
            background (bool, optional): Whether to run in the background. Defaults to True.
            owner_id (Optional[str], optional): Owner ID. Defaults to None.

        Returns:
            Optional[int]: An optional PID of the proxy
        """
        if local_port is None:
            local_port = find_open_port(container_port, container_port + 1000)
            if not local_port:
                raise RuntimeError("Failed to find an open port")

        cmd = f"kubectl port-forward pod/{self._get_pod_name(name)} {local_port}:{container_port} -n {self.namespace}"

        print("executing command: ", cmd)
        if background:
            print("running in background")
            proc = subprocess.Popen(
                cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            print("proc.pid: ", proc.pid)
            self._register_cleanup(proc)
            return (local_port, proc.pid)  # Return the PID of the subprocess

        else:
            try:
                subprocess.run(cmd, shell=True, check=True)
                return (
                    local_port,
                    None,
                )  # No PID to return when not in background mode
            except subprocess.CalledProcessError as e:
                raise RuntimeError(f"Port forwarding failed: {e}")

    def logs(
        self,
        name: str,
        follow: bool = False,
        owner_id: Optional[str] = None,
    ) -> Union[str, Iterator[str]]:
        """
        Fetches the logs from the specified pod. Can return all logs as a single string,
        or stream the logs as a generator of strings.

        Parameters:
            name (str): The name of the pod.
            follow (bool): Whether to continuously follow the logs.
            owner_id (Optional[str]): The owner ID of the pod. If provided, it will be included in the log lines.

        Returns:
            Union[str, Iterator[str]]: All logs as a single string, or a generator that yields log lines.
        """
        try:
            return self.core_api.read_namespaced_pod_log(
                name=self._get_pod_name(name),
                namespace=self.namespace,
                follow=follow,
                pretty="true",
                _preload_content=False,  # Important to return a generator when following
            )
        except ApiException as e:
            print(f"Failed to get logs for pod '{name}': {e}")
            raise

    def create_secret(self, name: str, env_vars: dict) -> client.V1Secret:
        """
        Creates a Kubernetes Secret object to store environment variables.

        Parameters:
            name (str): The base name of the secret, usually related to the pod name.
            env_vars (dict): A dictionary containing the environment variables as key-value pairs.

        Returns:
            client.V1Secret: The created Kubernetes Secret object.
        """
        logger.debug("creating secret with envs: ", env_vars)
        secret = client.V1Secret(
            api_version="v1",
            kind="Secret",
            metadata=client.V1ObjectMeta(
                name=self._get_pod_name(name),
                namespace=self.namespace,
                # This ensures that the secret is deleted when the pod is deleted.
                labels={"provisioner": "agentdesk"},
            ),
            string_data=env_vars,
            type="Opaque",
        )
        try:
            self.core_api.create_namespaced_secret(
                namespace=self.namespace, body=secret
            )
            print(f"Secret created '{self._get_pod_name(name)}'")
            return secret
        except ApiException as e:
            print(f"Failed to create secret: {e}")
            raise

    def runtime_local_addr(self, name: str, owner_id: Optional[str] = None) -> str:
        """
        Returns the local address of agentd with respect to the runtime
        """
        instances = DesktopInstance.find(name=name, owner_id=owner_id)
        if not instances:
            raise ValueError(f"No instances found for name '{name}'")
        instance = instances[0]

        return f"http://{instance.name}.{self.namespace}.svc.cluster.local:8000"

    def clean(
        self,
        owner_id: Optional[str] = None,
    ) -> None:
        pods = self.core_api.list_namespaced_pod(
            namespace="default", label_selector="provisioner=agentdesk"
        )
        for pod in pods.items:
            try:
                self.core_api.delete_namespaced_pod(
                    name=pod.metadata.name,
                    namespace="default",
                    body=client.V1DeleteOptions(grace_period_seconds=5),
                )
                print(f"Deleted pod: {pod.metadata.name}")
            except ApiException as e:
                print(f"Failed to delete pod '{pod.metadata.name}': {e}")
