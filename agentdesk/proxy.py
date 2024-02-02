import subprocess
from typing import List

import psutil

from .util import check_port_in_use


def check_ssh_proxy_running(port: int, ssh_user: str, ssh_host: str) -> bool:
    """Check if an SSH proxy process is running with the given user, host, and port."""
    search_command = f"ssh -N -L {port}:{ssh_host}:{port} -p 2222 {ssh_user}@{ssh_host}"
    for proc in psutil.process_iter(["cmdline"]):
        try:
            cmdline: List[str] = proc.info["cmdline"]
            if cmdline and search_command in " ".join(
                cmdline
            ):  # Ensure cmdline is not None and is a list
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass  # Process has terminated or we don't have permission to access its info
    return False


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


def cleanup_proxy(proxy_process: subprocess.Popen) -> None:
    """Terminate the SSH proxy subprocess if it's running"""

    if proxy_process:
        proxy_process.terminate()
        print("SSH proxy terminated.")


def ensure_ssh_proxy(
    port: int = 6080, ssh_user: str = "agentsea", ssh_host: str = "localhost"
) -> None:
    """Ensure that an SSH proxy is running"""

    if not check_ssh_proxy_running(port, ssh_user, ssh_host):
        print("ssh proxy not found, starting one...")
        setup_ssh_proxy(port, ssh_user, ssh_host)
    else:
        print("ssh proxy already running")
