from __future__ import annotations
import subprocess
from typing import Optional, NoReturn, Tuple

import psutil
import paramiko
from paramiko import SSHClient, RSAKey
import requests

from .util import check_port_in_use, find_open_port


class SSHConnection:
    """Establish an SSH connection to a remote host"""

    def __init__(
        self,
        hostname: str = "localhost",
        port: int = 2222,
        username: str = "agentsea",
        key_path: str = "~/.ssh/id_rsa",
        local_bind_port: Optional[int] = None,
        remote_bind_address: str = "localhost",
        remote_bind_port: int = 8000,
    ):
        if not local_bind_port:
            local_bind_port = find_open_port(8000)

        self.hostname = hostname
        self.port = port
        self.username = username
        self.key_path = key_path
        self.local_bind_port = local_bind_port
        self.remote_bind_address = remote_bind_address
        self.remote_bind_port = remote_bind_port
        self.client = SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    def connect(self) -> bool:
        """Attempt to establish the SSH connection and set up port forwarding."""
        try:
            self.client = SSHClient()
            self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            self.rsa_key = RSAKey(filename=self.key_path)
            self.client.connect(
                hostname=self.hostname,
                port=self.port,
                username=self.username,
                pkey=self.rsa_key,
            )
            self._forward_port()
            return True
        except Exception as e:
            print("Could not connect to SSH server:", e)
            return False

    def __enter__(self) -> "SSHConnection":
        # Assume connection is already established before entering the context
        if self.client is None:
            self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self.client:
            self.client.close()

    def _forward_port(self) -> None:
        transport = self.client.get_transport()
        transport.request_port_forward("", self.local_bind_port)
        transport.open_channel(
            "direct-tcpip",
            (self.remote_bind_address, self.remote_bind_port),
            ("localhost", self.local_bind_port),
        )
        print(
            f"Port forwarding set up: localhost:{self.local_bind_port} -> {self.remote_bind_address}:{self.remote_bind_port}"
        )

    def check_connection(self) -> Optional[str]:
        """Check if the SSH connection and port forwarding work by sending a request."""
        try:
            response = requests.get(f"http://localhost:{self.local_bind_port}")
            return f"Connection successful: {response.status_code}"
        except Exception as e:
            return f"Connection failed: {e}"


def check_ssh_proxy_running(port: int, ssh_user: str, ssh_host: str) -> Optional[int]:
    """Check if an SSH proxy process is running with the given user, host, and port, and return its PID."""
    search_command = f"ssh -N -L {port}:{ssh_host}:{port} -p 2222 {ssh_user}@{ssh_host}"
    for proc in psutil.process_iter(["cmdline", "pid"]):
        try:
            cmdline: list[str] = proc.info["cmdline"]
            if cmdline and search_command in " ".join(
                cmdline
            ):  # Check if command line matches
                return proc.info["pid"]  # Return the PID of the process
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass  # Process has terminated or we don't have permission to access its info
    return None


def setup_ssh_proxy(
    port: int = 6080, ssh_user: str = "agentsea", ssh_host: str = "localhost"
) -> subprocess.Popen:
    """Set up an SSH proxy if it's not already running"""

    if check_port_in_use(port):
        print(f"Port {port} is already in use. Assuming SSH proxy is running.")
        return None

    ssh_command = f"ssh -N -L {port}:localhost:{port} -p 2222 {ssh_user}@{ssh_host}"
    proxy_process = subprocess.Popen(ssh_command, shell=True)
    print(f"SSH proxy setup on port {port}")
    return proxy_process


def cleanup_proxy(pid: int) -> None:
    """Terminate the SSH proxy process with the given PID."""
    try:
        proc = psutil.Process(pid)
        proc.terminate()  # Terminate the process
        proc.wait()  # Wait for the process to terminate
        print(f"SSH proxy with PID {pid} terminated.")
    except psutil.NoSuchProcess:
        print(f"No process found with PID {pid}.")
    except psutil.AccessDenied:
        print(f"Access denied when trying to terminate the process with PID {pid}.")
    except Exception as e:
        print(
            f"An error occurred while trying to terminate the process with PID {pid}: {e}"
        )


def ensure_ssh_proxy(
    port: int = 6080, ssh_user: str = "agentsea", ssh_host: str = "localhost"
) -> int:
    """Ensure that an SSH proxy is running"""

    pid = check_ssh_proxy_running(port, ssh_user, ssh_host)
    if pid:
        print("existing ssh proxy found")
        return pid
    print("ssh proxy not found, starting one...")
    process = setup_ssh_proxy(port, ssh_user, ssh_host)
    return process.pid
