from __future__ import annotations
import os
import subprocess
from typing import Optional, Generator
import threading
import socket
import select
import time
import contextlib
import tempfile

import paramiko
import psutil


class SSHPortForwarding:
    """Port forwarding using SSH"""

    def __init__(
        self,
        local_port: int = 8001,
        remote_host: str = "localhost",
        remote_port: int = 8000,
        ssh_host: str = "localhost",
        ssh_port: int = 2222,
        username: str = "agentsea",
        key_file: str = "~/.ssh/id_rsa",
    ) -> None:
        self.local_port = local_port
        self.remote_host = remote_host
        self.remote_port = remote_port
        self.ssh_host = ssh_host
        self.ssh_port = ssh_port
        self.username = username
        self.key_file = os.path.expanduser(key_file)
        self.client = paramiko.SSHClient()
        self.server = None
        self.threads = []
        self.active = True

    def __enter__(self):
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.client.connect(
            hostname=self.ssh_host,
            port=self.ssh_port,
            username=self.username,
            key_filename=self.key_file,
        )
        self.transport = self.client.get_transport()

        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(
            socket.SOL_SOCKET, socket.SO_REUSEADDR, 1
        )  # Set SO_REUSEADDR
        self.server.bind(("localhost", self.local_port))
        self.server.listen(100)
        print(f"Listening for connections on localhost:{self.local_port}")

        self.shutdown_event = threading.Event()
        threading.Thread(target=self.accept_connections, daemon=True).start()
        return self

    def accept_connections(self):
        while not self.shutdown_event.is_set():
            ready, _, _ = select.select([self.server], [], [], 0.5)
            if ready and not self.shutdown_event.is_set():
                try:
                    client_socket, addr = self.server.accept()
                    print(f"Received connection from {addr}")
                    if (
                        self.shutdown_event.is_set()
                    ):  # Check again to avoid handling during shutdown
                        client_socket.close()
                        break
                    thread = threading.Thread(
                        target=self.handle_client, args=(client_socket,)
                    )
                    thread.daemon = True
                    self.threads.append(thread)
                    thread.start()
                except Exception as e:
                    if not self.shutdown_event.is_set():
                        print(f"Error accepting connections: {e}")
                    break

    def handle_client(self, client_socket):
        try:
            channel = self.transport.open_channel(
                kind="direct-tcpip",
                dest_addr=(self.remote_host, self.remote_port),
                src_addr=client_socket.getpeername(),
            )
            if channel is None:
                raise Exception("Channel opening failed.")
        except Exception as e:
            print(f"Forwarding failed: {e}")
            client_socket.close()
            return

        while True:
            data = client_socket.recv(1024)
            if not data:
                break
            channel.send(data)
            data = channel.recv(1024)
            if not data:
                break
            client_socket.send(data)

        channel.close()
        client_socket.close()

    def __exit__(self, exc_type, exc_val, exc_tb):
        print("Exiting SSH port forwarding context...")

        self.shutdown_event.set()

        # Signal the accept_connections loop to stop
        self.active = False

        try:
            # Attempt to unblock the server.accept() by connecting to the server socket.
            # This is a workaround for the blocking accept call and ensures it exits gracefully.
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as temp_sock:
                temp_sock.settimeout(1)
                try:
                    temp_sock.connect(("localhost", self.local_port))
                except socket.error:
                    pass  # Ignore errors here as we're just trying to unblock accept()
        finally:
            # Close the server socket to ensure no new connections are accepted
            print("Closing server socket...")
            self.server.close()

            # Closing the SSH client connection
            print("Closing SSH client...")
            self.client.close()

        print("SSH tunnel and all related resources have been closed.")


def check_ssh_proxy_running(
    local_port: int, remote_port: int, ssh_port: int, ssh_user: str, ssh_host: str
) -> Optional[int]:
    """Check if an SSH proxy process is running based on the local and remote port, SSH port, user, and host."""

    # Construct a partial command pattern to match.
    # Since we don't know the temporary key file name, we'll omit it from the search.
    partial_command_pattern = (
        f"-L 127.0.0.1:{local_port}:localhost:{remote_port} "
        f"-p {ssh_port} {ssh_user}@{ssh_host}"
    )

    for proc in psutil.process_iter(["cmdline", "pid"]):
        try:
            cmdline: list[str] = proc.info["cmdline"]
            cmdline_str = " ".join(cmdline)
            if partial_command_pattern in cmdline_str:
                return proc.info["pid"]
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass

    return None


def setup_ssh_proxy(
    local_port: int = 6080,
    remote_port: int = 6080,
    ssh_port: int = 22,
    ssh_user: str = "agentsea",
    ssh_host: str = "localhost",
    ssh_key: Optional[str] = None,
) -> Optional[subprocess.Popen]:
    """Set up an SSH proxy if it's not already running."""

    # Handle SSH key temporary file creation
    temp_key_file = None
    if ssh_key:
        temp_key_file = tempfile.NamedTemporaryFile(delete=False)
        temp_key_file.write(ssh_key.encode())
        temp_key_file.close()

    ssh_command = (
        "ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
        f"-N -L 127.0.0.1:{local_port}:localhost:{remote_port} -p {ssh_port} "
    )
    if ssh_key:
        ssh_command += f"-i {temp_key_file.name} "
    ssh_command += f"{ssh_user}@{ssh_host}"

    print("Executing command: ", ssh_command)
    try:
        proxy_process = subprocess.Popen(
            ssh_command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        # Give it a moment to fail, SSH should exit immediately if there's an error
        time.sleep(1)
        if proxy_process.poll() is not None:
            _, err = proxy_process.communicate()
            print(f"SSH proxy failed to start. Error: {err.decode()}")
            return None
    except Exception as e:
        print(f"Error starting SSH proxy: {e}")
        raise
    finally:
        if temp_key_file:
            os.unlink(temp_key_file.name)  # Clean up the temporary file

    print(f"SSH proxy setup on local port {local_port}")
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
    local_port: int = 6080,
    remote_port: int = 6080,
    ssh_port: int = 22,
    ssh_user: str = "agentsea",
    ssh_host: str = "localhost",
    ssh_key: Optional[str] = None,
) -> int:
    """Ensure that an SSH proxy is running and return its PID."""
    pid = check_ssh_proxy_running(local_port, remote_port, ssh_port, ssh_user, ssh_host)
    if pid:
        print("Existing SSH proxy found.")
        return pid  # PID of the already running process

    print("SSH proxy not found, starting one...")
    process = setup_ssh_proxy(
        local_port, remote_port, ssh_port, ssh_user, ssh_host, ssh_key
    )
    if process is None:
        # If setup_ssh_proxy returned None, it means the port is in use but no PID was found.
        # It might be necessary to refine check_ssh_proxy_running or setup_ssh_proxy to ensure consistency.
        raise RuntimeError(
            f"Failed to start SSH proxy on local port {local_port}, and no existing process was found."
        )
    time.sleep(1)  # Adjust sleep time as needed
    return process.pid  # Assuming the process started successfully


@contextlib.contextmanager
def ensure_managed_ssh_proxy(
    local_port: int,
    remote_port: int,
    ssh_port: int,
    ssh_user: str,
    ssh_host: str,
    ssh_key: Optional[str] = None,
) -> Generator[Optional[int], None, None]:
    pid: Optional[int] = check_ssh_proxy_running(
        local_port, remote_port, ssh_port, ssh_user, ssh_host
    )
    process_started: bool = False

    if pid is None:
        print("SSH proxy not found, starting one...")
        process = setup_ssh_proxy(
            local_port, remote_port, ssh_port, ssh_user, ssh_host, ssh_key
        )
        if process is None:
            raise RuntimeError("Failed to ensure SSH proxy is running.")
        pid = process.pid
        process_started = True

    try:
        yield pid
    finally:
        if process_started:
            print(f"Cleaning up newly started SSH proxy with PID {pid}...")
            cleanup_proxy(pid)
