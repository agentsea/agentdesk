import atexit
import base64
import json
import logging
import os
import random
import signal
import string
import subprocess
import sys
import httpx
from typing import (
    Any,
    Dict,
    Iterator,
    List,
    Literal,
    Optional,
    Tuple,
    Type,
    TypeVar,
    Union,
)

import shortuuid
from google.auth.transport.requests import Request
from google.cloud import container_v1
from google.oauth2 import service_account
from kubernetes import client, config
from kubernetes.client.rest import ApiException
from namesgenerator import get_random_name
from pydantic import BaseModel
from tenacity import retry, stop_after_attempt, wait_fixed

from agentdesk.util import find_open_port
from .base import DesktopInstance, DesktopProvider, V1ProviderData

logger = logging.getLogger(__name__)

ENABLE_NETWORK_POLICY = os.getenv("ENABLE_NETWORK_POLICY", "false").lower() == "true"


class GKEOpts(BaseModel):
    cluster_name: str
    region: str
    service_account_json: str


class LocalOpts(BaseModel):
    path: Optional[str] = os.getenv("KUBECONFIG", os.path.expanduser("~/.kube/config"))


class KubeConnectConfig(BaseModel):
    provider: Literal["gke", "local"] = "local"
    namespace: str = "default"
    gke_opts: Optional[GKEOpts] = None
    local_opts: Optional[LocalOpts] = None
    branch: Optional[str] = None


def gke_opts_from_env(
    gke_sa_json=os.getenv("GKE_SA_JSON"),
    cluster_name=os.getenv("CLUSTER_NAME"),
    region=os.getenv("CLUSTER_REGION"),
) -> GKEOpts:
    if not gke_sa_json:
        raise ValueError("GKE_SA_JSON not set")
    if not cluster_name:
        raise ValueError("CLUSTER_NAME not set")
    if not region:
        raise ValueError("CLUSTER_REGION not set")
    return GKEOpts(
        service_account_json=gke_sa_json,
        cluster_name=cluster_name,
        region=region,
    )


DP = TypeVar("DP", bound="KubernetesProvider")


class KubernetesProvider(DesktopProvider):
    """A provider of desktop virtual machines"""

    def __init__(self, cfg: Optional[KubeConnectConfig] = None) -> None:
        self.cfg = cfg or KubeConnectConfig()

        self.kubeconfig = None
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
                self.kubeconfig = opts.path
        else:
            raise ValueError("Unsupported provider: " + cfg.provider)

        self.core_api = client.CoreV1Api()
        self.networking_api = client.NetworkingV1Api()

        self.namespace = cfg.namespace

        self.subprocesses = []
        self.setup_signal_handlers()

        self.branch = cfg.branch

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
        enable_basic_auth: bool = False,
        password: Optional[str] = None,
        sub_folder: Optional[str] = None,
        id: Optional[str] = None,
        ttl: Optional[int] = None,
        assigned: Optional[float] = None,
        node_selector: Optional[Dict[str, str]] = None,
        tolerations: Optional[List[client.V1Toleration]] = None,
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
            enable_basic_auth (bool, optional): Enable basic auth. Defaults to False.
            password (str, optional): Password to use. Defaults to None.
            sub_folder (str, optional): Subfolder to use. Defaults to None.
            id (str, optional): ID of the desktop. Defaults to None.
            ttl (int, optional): Time to live seconds for the desktop. Defaults to None.

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

        basic_auth_password = password
        if not password:
            basic_auth_password = "".join(
                random.choice(string.ascii_letters + string.digits) for _ in range(24)
            )
        basic_auth_user = None
        if enable_basic_auth:
            basic_auth_user = id
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
            env=[client.V1EnvVar(name="CONTAINER_NAME", value=name)],
            image_pull_policy="Always",
        )

        # Pod specification
        pod_spec = client.V1PodSpec(
            containers=[container],
            restart_policy="Never",
            automount_service_account_token=False,
            node_selector=node_selector,
            tolerations=tolerations,
        )

        # Pod creation
        pod = client.V1Pod(
            api_version="v1",
            kind="Pod",
            metadata=client.V1ObjectMeta(
                name=pod_name,
                labels={
                    "provisioner": "agentdesk",
                    "app": pod_name,
                    "workload": "desktop",
                    "branch": self.branch if self.branch else "undefined",
                },
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

        if ENABLE_NETWORK_POLICY:
            self.create_network_policy(name)

        # Now, create the Service
        service_name = pod_name

        service = client.V1Service(
            api_version="v1",
            kind="Service",
            metadata=client.V1ObjectMeta(
                name=service_name,
                labels={"provisioner": "agentdesk"},
            ),
            spec=client.V1ServiceSpec(
                selector={"app": pod_name},
                ports=[
                    client.V1ServicePort(
                        name="agentd",
                        port=8000,
                        target_port=8000,
                    ),
                    client.V1ServicePort(
                        name="vnc",
                        port=3000,
                        target_port=3000,
                    ),
                    client.V1ServicePort(
                        name="vnc-https",
                        port=3001,
                        target_port=3001,
                    ),
                ],
                type="ClusterIP",
            ),
        )

        try:
            created_service = self.core_api.create_namespaced_service(
                namespace=self.namespace, body=service
            )
            print(f"Service created with name '{service_name}'")
        except ApiException as e:
            print(f"Exception when creating service: {e}")
            # Optionally, delete the Pod if Service creation fails
            self.core_api.delete_namespaced_pod(
                name=pod_name,
                namespace=self.namespace,
                body=client.V1DeleteOptions(grace_period_seconds=5),
            )
            raise

        self.wait_pod_ready(name)
        self.wait_for_http_200(service_name)

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
            image=image,
            resource_name=pod_name,
            namespace=self.namespace,
            basic_auth_user=basic_auth_user,
            basic_auth_password=basic_auth_password,
            ttl=ttl,
            assigned=assigned,
        )

        return instance

    def patch_meta_owner(self, owner_id, pod_name) -> client.V1Pod:
        """
        Patch the metadata of a Kubernetes pod to update the owner annotation.

        Args:
            owner_id (str): The new owner ID to set in the annotations.
            pod_name (str): The name of the pod to patch.

        Returns:
            V1Pod: The updated pod object.

        Raises:
            ApiException: If the patch request fails.
        """

        patch = {"metadata": {"annotations": {"owner": owner_id}}}

        try:
            updated_pod = self.core_api.patch_namespaced_pod(
                name=self._get_pod_name(pod_name),
                namespace=self.namespace,
                body=patch,
            )
            print(
                f"Pod '{pod_name}' updated successfully with new owner '{owner_id}'.",
                flush=True,
            )
        except ApiException as e:
            print(f"Failed to update pod '{pod_name}' with Error: {e}", flush=True)
            raise

        return updated_pod

    def create_network_policy(self, name: str) -> None:
        """
        Creates a NetworkPolicy that restricts the pod's network access.
        It allows egress to the internet while denying access to the cluster's internal network.
        """
        pod_name = self._get_pod_name(name)
        policy = client.V1NetworkPolicy(
            metadata=client.V1ObjectMeta(
                name=pod_name,
                namespace=self.namespace,
                labels={"provisioner": "agentdesk"},
            ),
            spec=client.V1NetworkPolicySpec(
                pod_selector=client.V1LabelSelector(match_labels={"app": pod_name}),
                policy_types=["Egress"],
                egress=[
                    client.V1NetworkPolicyEgressRule(
                        to=[
                            client.V1NetworkPolicyPeer(
                                ip_block=client.V1IPBlock(
                                    cidr="0.0.0.0/0",
                                    _except=[
                                        # Exclude common private IP ranges
                                        "10.0.0.0/8",
                                        "172.16.0.0/12",
                                        "192.168.0.0/16",
                                        "100.64.0.0/10",
                                    ],
                                )
                            )
                        ]
                    )
                ],
            ),
        )
        try:
            self.networking_api.create_namespaced_network_policy(
                namespace=self.namespace, body=policy
            )
            print(f"NetworkPolicy created for pod '{pod_name}'")
        except ApiException as e:
            print(f"Failed to create NetworkPolicy: {e}")
            raise

    def delete(self, name: str, owner_id: Optional[str] = None) -> None:
        """Delete a desktop

        Args:
            name (str): Name of the desktop
            owner_id (str, optional): Owner of the desktop. Defaults to None
        """
        errors = []
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
            errors.append(e)

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
                errors.append(e)

        try:
            self.core_api.delete_namespaced_service(
                name=pod_name,
                namespace=self.namespace,
                body=client.V1DeleteOptions(),
            )
            print(f"Successfully deleted service: {self._get_pod_name(name)}")
        except ApiException as e:
            if e.status == 404:
                print(
                    f"Service '{self._get_pod_name(name)}' not found, skipping deletion."
                )
            else:
                print(f"Failed to delete service '{self._get_pod_name(name)}': {e}")
                errors.append(e)

        try:
            self.networking_api.delete_namespaced_network_policy(
                name=pod_name,
                namespace=self.namespace,
                body=client.V1DeleteOptions(),
            )
            print(f"Successfully deleted NetworkPolicy: {pod_name}")
        except ApiException as e:
            if e.status == 404:
                print(f"NetworkPolicy '{pod_name}' not found, skipping deletion.")
            else:
                print(f"Failed to delete NetworkPolicy '{pod_name}': {e}")
                errors.append(e)

        if errors:
            raise Exception(errors)

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
        cfg = self.cfg
        cfg.gke_opts = None

        return V1ProviderData(
            type="kube",
            args={"cfg": cfg.model_dump_json()},
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

            if data.type == "gke":
                config.gke_opts = gke_opts_from_env()

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
    def connect_to_gke(self, opts: GKEOpts) -> Tuple[client.CoreV1Api, str, str, dict]:
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
        self.kubeconfig = kubeconfig

        config.load_kube_config_from_dict(config_dict=kubeconfig)
        v1_client = client.CoreV1Api()
        logger.debug("K8s returning client...")

        return v1_client, project_id, cluster_name, kubeconfig

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
        data = data or {}
        headers = headers or {}

        workload_proxy_url = os.getenv("WORKLOAD_PROXY_URL")
        if workload_proxy_url is not None:
            print("Using workload proxy at", workload_proxy_url)
            client_cert = os.getenv("WORKLOAD_PROXY_CLIENT_CERT")
            client_key = os.getenv("WORKLOAD_PROXY_CLIENT_KEY")
            ca_cert = os.getenv("WORKLOAD_PROXY_CA_CERT")

            workload_proxy_client = httpx.Client(
                verify=ca_cert, cert=(client_cert, client_key)
            )

            merged_headers = {
                **headers,
                "X-Pod-Name": name,
                "X-Namespace": self.cfg.namespace,
                "X-Port": str(port),
            }
        else:
            print("Using direct connection to workload service")
            workload_proxy_client = httpx.Client()
            merged_headers = headers
            workload_proxy_url = (
                f"http://{name}.{self.cfg.namespace}.svc.cluster.local:{port}"
            )

        json_data = None if method == "GET" else data
        query_parameters = ""
        if method == "GET" and data:
            query_parameters = "?" + "&".join([f"{k}={v}" for k, v in data.items()])

        url = f"{workload_proxy_url.rstrip('/')}/{path.lstrip('/')}" + query_parameters

        print("Method: ", method)
        print("URL: ", url)
        print("Headers: ", merged_headers)
        print("JSON Data: ", json_data)

        r = workload_proxy_client.request(
            method=method, url=url, headers=merged_headers, json=json_data
        )

        return r.status_code, r.text

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
            Tuple[int, Optional[int]]: The local port being forwarded, and optionally the PID of the subprocess.
        """
        if local_port is None:
            local_port = find_open_port(container_port, container_port + 1000)
            if not local_port:
                raise RuntimeError("Failed to find an open port")

        # Prepare environment variables for the subprocess
        env = os.environ.copy()
        if self.kubeconfig:
            # Write kubeconfig to a temporary file
            import tempfile

            import yaml

            kubeconfig_file = tempfile.NamedTemporaryFile(delete=False, mode="w")
            yaml.dump(self.kubeconfig, kubeconfig_file)
            kubeconfig_file.close()
            env["KUBECONFIG"] = kubeconfig_file.name

        cmd = f"kubectl port-forward pod/{self._get_pod_name(name)} {local_port}:{container_port} -n {self.namespace}"

        print("Executing command:", cmd)
        if background:
            print("Running in background")
            proc = subprocess.Popen(
                cmd,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,  # Pass the environment variables to the subprocess
            )
            print("Process PID:", proc.pid)
            self._register_cleanup(proc)
            # Store the PID of the process in the class
            return (local_port, proc.pid)  # Return the PID of the subprocess
        else:
            try:
                subprocess.run(cmd, shell=True, check=True, env=env)
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
