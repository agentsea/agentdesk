from __future__ import annotations

import atexit
import contextlib
import logging
import os
import select
import socket
import subprocess
import threading
import time
from typing import Generator, Optional

import paramiko
import psutil

from .config import AGENTSEA_KEY_DIR
from .util import generate_short_hash

logger = logging.getLogger(__name__)


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
                    client_socket, addr = self.server.accept()  # type: ignore
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
            channel = self.transport.open_channel(  # type: ignore
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
            self.server.close()  # type: ignore

            # Closing the SSH client connection
            print("Closing SSH client...")
            self.client.close()

        print("SSH tunnel and all related resources have been closed.")


def check_ssh_proxy_running(
    local_port: int, remote_port: int, ssh_port: int, ssh_user: str, ssh_host: str
) -> Optional[int]:
    """Check if an SSH proxy process is running based on the local and remote port, SSH port, user, and host."""

    logger.debug("checking if ssh proxy is running...")
    # Construct a partial command pattern to match.
    # Since we don't know the temporary key file name, we'll omit it from the search.
    partial_command_pattern = (
        f"-L 127.0.0.1:{local_port}:localhost:{remote_port} "
        f"-p {ssh_port} {ssh_user}@{ssh_host}"
    )

    for proc in psutil.process_iter(["cmdline", "pid"]):
        try:
            if "cmdline" not in proc.info:  # type: ignore
                continue
            cmdline: list[str] = proc.info["cmdline"]  # type: ignore
            cmdline_str = " ".join(cmdline)
            if partial_command_pattern in cmdline_str:
                return proc.info["pid"]  # type: ignore
        except Exception:
            pass

    return None


def cleanup_ssh_key(filepath: str) -> None:
    """Terminate the SSH proxy process with the given PID."""

    try:
        os.unlink(filepath)
    except Exception as e:
        pass


def setup_ssh_proxy(
    local_port: int = 6080,
    remote_port: int = 6080,
    ssh_port: int = 22,
    ssh_user: str = "agentsea",
    ssh_host: str = "localhost",
    ssh_key: Optional[str] = None,
    log_error: bool = True,
    bind_addr: str = "0.0.0.0",
) -> Optional[subprocess.Popen]:
    """Set up an SSH proxy if it's not already running."""

    # Handle SSH key temporary file creation
    key_filepath = None
    if ssh_key:
        os.makedirs(AGENTSEA_KEY_DIR, exist_ok=True)
        os.chmod(AGENTSEA_KEY_DIR, 0o700)

        key_filepath = os.path.join(
            AGENTSEA_KEY_DIR, f"id_rsa_{generate_short_hash(ssh_key)}"
        )

        with open(key_filepath, "wb") as f:
            f.write(ssh_key.encode())

        os.chmod(key_filepath, 0o600)

    ssh_command = (
        "ssh "
        "-o StrictHostKeyChecking=no "
        "-o UserKnownHostsFile=/dev/null "
        "-o IdentitiesOnly=yes "
        "-N "
        f"-L {bind_addr}:{local_port}:localhost:{remote_port} "
        f"-p {ssh_port} "
    )
    if ssh_key:
        ssh_command += f"-i {key_filepath} "  # type: ignore
    ssh_command += f"{ssh_user}@{ssh_host}"

    logger.debug(f"Executing command: {ssh_command}")
    try:
        proxy_process = subprocess.Popen(
            ssh_command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        # Give it a moment to fail, SSH should exit immediately if there's an error
        time.sleep(1)
        if proxy_process.poll() is not None:
            _, err = proxy_process.communicate()
            if log_error:
                logger.error(f"SSH proxy failed to start. Error: {err.decode()}")
            if key_filepath:
                os.unlink(key_filepath)
            raise Exception(f"SSH proxy failed to start: Error: {err.decode()}")
    except Exception as e:
        if log_error:
            logger.error(f"Error starting SSH proxy: {e}")
        raise

    logger.debug(f"SSH proxy setup on local port {local_port}")
    if key_filepath:
        atexit.register(cleanup_ssh_key, key_filepath)
    return proxy_process


def cleanup_proxy(pid: int, log_error: bool = True) -> None:
    """Terminate the SSH proxy process with the given PID."""

    try:
        proc = psutil.Process(pid)
        proc.terminate()  # Terminate the process
        proc.wait()  # Wait for the process to terminate
        logger.debug(f"SSH proxy with PID {pid} terminated.")
    except psutil.NoSuchProcess:
        if log_error:
            logger.error(f"No process found with PID {pid}.")
    except psutil.AccessDenied:
        if log_error:
            logger.error(
                f"Access denied when trying to terminate the process with PID {pid}."
            )
    except Exception as e:
        if log_error:
            logger.error(
                f"An error occurred while trying to terminate the process with PID {pid}: {e}"
            )


def ensure_ssh_proxy(
    local_port: int = 6080,
    remote_port: int = 6080,
    ssh_port: int = 22,
    ssh_user: str = "agentsea",
    ssh_host: str = "localhost",
    ssh_key: Optional[str] = None,
    log_error: bool = True,
    bind_addr: str = "0.0.0.0",
) -> int:
    """Ensure that an SSH proxy is running and return its PID.

    Args:
        local_port (int, optional): Local port. Defaults to 6080.
        remote_port (int, optional): Remote port. Defaults to 6080.
        ssh_port (int, optional): SSH port. Defaults to 22.
        ssh_user (str, optional): SSH user. Defaults to "agentsea".
        ssh_host (str, optional): SSH host. Defaults to "localhost".
        ssh_key (Optional[str], optional): SSH private key. Defaults to None.
        log_error (bool, optional): Whether to log errors. Defaults to True.
        bind_addr (str, optional): Bind address. Defaults to "0.0.0.0".

    Returns:
        int: A process pid.
    """
    pid = None
    try:
        pid = check_ssh_proxy_running(
            local_port, remote_port, ssh_port, ssh_user, ssh_host
        )
    except Exception as e:
        if log_error:
            logger.error(f"Failed to check if proxy is running: {e}")
        pass
    if pid:
        logger.debug("Existing SSH proxy found.")
        return pid  # PID of the already running process

    logger.debug("SSH proxy not found, starting one...")
    process = setup_ssh_proxy(
        local_port,
        remote_port,
        ssh_port,
        ssh_user,
        ssh_host,
        ssh_key,
        log_error=log_error,
        bind_addr=bind_addr,
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
        logger.debug("SSH proxy not found, starting one...")
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
            logger.debug(f"Cleaning up newly started SSH proxy with PID {pid}...")
            cleanup_proxy(pid)
