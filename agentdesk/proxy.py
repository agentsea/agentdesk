import subprocess
from typing import Optional

import psutil

from .util import check_port_in_use


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
