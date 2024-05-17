from typing import Optional
import typer
import shutil
import os
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as pkgversion

from tabulate import tabulate
from namesgenerator import get_random_name

from agentdesk.server.models import V1ProviderData
from agentdesk.vm.load import load_provider
from agentdesk.vm import DesktopVM
from agentdesk.util import convert_unix_to_datetime
from agentdesk.key import SSHKeyPair
from agentdesk.config import AGENTSEA_HOME

app = typer.Typer(no_args_is_help=True)

# Global option to enable dev-specific commands
dev_mode: bool = typer.Option(False, "--dev", help="Enable developer-specific commands")


try:
    __version__ = pkgversion("agentdesk")
except PackageNotFoundError:
    # Fallback version or error handling
    __version__ = "unknown"


@app.command(help="Show the version of the CLI")
def version():
    """Show the CLI version."""
    typer.echo(__version__)


@app.callback()
def main(dev: bool = dev_mode):
    global dev_mode
    dev_mode = dev

    if dev_mode:
        print(f"dev_mode: {dev_mode}")


@app.command(help="Create a desktop.")
def create(
    name: Optional[str] = typer.Option(
        None, help="The name of the desktop to create. Defaults to a generated name."
    ),
    provider: str = typer.Option(
        "qemu",
        help="The provider type for the desktop. Options are 'ec2', 'gce', and 'qemu'",
    ),
    image: Optional[str] = typer.Option(
        None, help="The image to use for the desktop. Defaults to Ubuntu Jammy."
    ),
    memory: int = typer.Option(4, help="The amount of memory (in GB) for the desktop."),
    cpu: int = typer.Option(2, help="The number of CPU cores for the desktop."),
    disk: str = typer.Option(
        "30gb",
        help="The disk size for the desktop. Format as '<size>gb'.",
    ),
    reserve_ip: bool = typer.Option(
        False,
        help="Whether to reserve an IP address for the desktop.",
    ),
):
    if not name:
        name = get_random_name(sep="-")

    data = V1ProviderData(type=provider)
    _provider = load_provider(data)

    print(f"Creating desktop '{name}' using '{provider}' provider")
    try:
        _provider.create(
            name=name,
            image=image,
            memory=memory,
            cpu=cpu,
            disk=disk,
            reserve_ip=reserve_ip,
        )
    except KeyboardInterrupt:
        print("Keyboard interrupt received, exiting...")
        return


@app.command(help="Get or list desktops.")
def get(
    name: Optional[str] = typer.Option(
        None,
        help="The name of the desktop to retrieve. If not provided, all desktops will be listed.",
    ),
    provider: Optional[str] = typer.Option(
        None, help="The provider type for the desktop."
    ),
):
    if name:
        desktop = DesktopVM.get(name)
        if not desktop:
            raise ValueError("desktop not found")
        if not desktop.provider:
            raise ValueError("no desktop provider")
        if provider and desktop.provider.type != provider:
            print(f"Desktop '{name}' not found")
            return

        _provider = load_provider(desktop.provider)
        if not desktop.reserved_ip:
            _provider.refresh(log=False)
            desktop = DesktopVM.get(name)
            if not desktop:
                print(f"Desktop '{name}' not found")
                return

        if desktop:
            print(desktop.to_v1_schema().model_dump_json(indent=2))
        else:
            print(f"Desktop '{name}' not found")
        return

    provider_is_refreshed = {}
    vms = DesktopVM.find()
    if not vms:
        print("No desktops found")
    else:
        table = []
        for desktop in vms:
            if not desktop.provider:
                continue
            if provider:
                if desktop.provider.type != provider:
                    continue
            _provider = load_provider(desktop.provider)

            if not provider_is_refreshed.get(desktop.provider.type):
                if not desktop.reserved_ip:
                    _provider.refresh(log=False)
                    provider_is_refreshed[desktop.provider.type] = True
                    desktop = DesktopVM.get(desktop.name)
                    if not desktop:
                        continue

            table.append(
                [
                    desktop.name,
                    desktop.addr,
                    desktop.ssh_port,
                    desktop.status,
                    convert_unix_to_datetime(int(desktop.created)),
                    desktop.provider.type,  # type: ignore
                    desktop.reserved_ip,
                ]
            )

        print(
            tabulate(
                table,
                headers=[
                    "Name",
                    "Address",
                    "SSH Port",
                    "Status",
                    "Created",
                    "Provider",
                    "Reserved IP",
                ],
            )
        )
        print("")


@app.command(help="Delete a desktop.")
def delete(
    name: str = typer.Argument(..., help="The name of the desktop to delete."),
):
    desktop = DesktopVM.get(name)
    if not desktop:
        print(f"Desktop '{name}' not found")
        return

    if not desktop.provider:
        raise ValueError("no desktop provider")

    _provider = load_provider(desktop.provider)

    print("refreshing provider...")
    _provider.refresh()
    desktop = DesktopVM.get(name)
    if not desktop:
        print(f"Desktop '{name}' not found")
        return

    print(f"Deleting '{name}' desktop...")
    _provider.delete(name)
    print(f"\nDesktop '{name}' successfully deleted")


@app.command(help="View a desktop in a browser.")
def view(
    name: str = typer.Argument(..., help="The name of the desktop to view."),
):
    desktop = DesktopVM.get(name)
    if not desktop:
        print(f"Desktop '{name}' not found")
        return

    if not desktop.reserved_ip:
        print("refreshing provider...")
        if not desktop.provider:
            raise ValueError("no desktop provider")
        _provider = load_provider(desktop.provider)
        _provider.refresh()
        desktop = DesktopVM.get(name)
        if not desktop:
            print(f"Desktop '{name}' not found")
            return

    desktop.view()


@app.command(help="Refresh a provider.")
def refresh(
    provider: str = typer.Argument(..., help="The provider type for the desktop.")
):
    data = V1ProviderData(type=provider)
    _provider = load_provider(data)
    _provider.refresh()
    print(f"\nProvider '{provider}' successfully refreshed")


@app.command(help="Stop a desktop.")
def stop(
    name: str = typer.Argument(..., help="The name of the desktop to stop."),
):
    desktop = DesktopVM.get(name)
    if not desktop:
        print(f"Desktop '{name}' not found")
        return

    if not desktop.provider:
        raise ValueError("no desktop provider")

    _provider = load_provider(desktop.provider)

    if not desktop.reserved_ip:
        print("refreshing provider...")
        _provider.refresh()
        desktop = DesktopVM.get(name)
        if not desktop:
            print(f"Desktop '{name}' not found")
            return

    print(f"Stopping desktop '{name}'...")
    _provider.stop(name)
    print(f"\nDesktop '{name}' successfully stopped")


@app.command(help="Start a desktop.")
def start(
    name: str = typer.Argument(..., help="The name of the desktop to start."),
):
    desktop = DesktopVM.get(name)
    if not desktop:
        print(f"Desktop '{name}' not found")
        return

    if not desktop.provider:
        raise ValueError("no desktop provider")

    _provider = load_provider(desktop.provider)

    if not desktop.reserved_ip:
        print("refreshing provider...")
        _provider.refresh()
        desktop = DesktopVM.get(name)
        if not desktop:
            print(f"Desktop '{name}' not found")
            return

    print(f"Starting desktop '{name}'...")
    _provider.start(name)
    print(f"\nDesktop '{name}' successfully started")


@app.command(help="Clean cache")
def clear_cache():
    vm_dir = os.path.join(AGENTSEA_HOME, "vms")
    shutil.rmtree(vm_dir)

    print(f"cleared cache in {vm_dir}")


# START Dev mode commands
@app.command(
    name="export-keypair",
    help="Export the decrypted private key and public key for a device. Requires dev mode enabled.",
)
def export_keypair(
    name: str = typer.Argument(
        ..., help="The name of the device to export the keys for."
    ),
):
    if not dev_mode:
        print("Developer mode is not enabled.")
        raise typer.Exit()
    keys = SSHKeyPair.find_name_starts_like(name=name)
    if not keys:
        print(f"No SSH keys found for device like '{name}'")
        return

    banner = r"""
    ╔══════════════════════════════════════════════════════════════════════╗
    ║                                                                      ║
    ║                        ███╗   ██╗███████╗ ██████╗                    ║
    ║                        ████╗  ██║██╔════╝██╔═══██╗                   ║
    ║                        ██╔██╗ ██║█████╗  ██║   ██║                   ║
    ║                        ██║╚██╗██║██╔══╝  ██║   ██║                   ║
    ║                   ██╗  ██║ ╚████║██║     ╚██████╔╝                   ║
    ║                   ╚═╝  ╚═╝  ╚═══╝╚═╝      ╚═════╝                    ║
    ║                                                                      ║
    ║                        I N F O R M A T I O N                         ║
    ║                                                                      ║
    ║  Securely manage the lifecycle of exported cryptographic material.   ║
    ║  Ensure private keys are stored and transmitted securely.            ║
    ║  Delete private keys when they are no longer needed.                 ║
    ║                                                                      ║
    ║  Stay informed. Stay secure. Protect your secrets.                   ║
    ║                                                                      ║
    ╚══════════════════════════════════════════════════════════════════════╝
    """
    print(banner)

    for key in keys:
        decrypted_key = SSHKeyPair.decrypt_private_key(key.private_key)
        private_key_file_name = f"{key.name}.pem"
        public_key_file_name = f"{key.name}.pub"

        with open(private_key_file_name, "w") as file:
            file.write(decrypted_key)
        os.chmod(
            private_key_file_name, 0o600
        )  # Set file mode to read/write for the owner only

        with open(public_key_file_name, "w") as file:
            file.write(key.public_key)
        os.chmod(
            public_key_file_name, 0o644
        )  # Set file mode to read/write for the owner, and read for others

        print(
            f"Decrypted private key for device '{key.name}' saved to {os.path.abspath(private_key_file_name)}"
        )
        print(
            f"Public key for device '{key.name}' saved to {os.path.abspath(public_key_file_name)}"
        )


@app.command(
    name="list-keys",
    help="List all SSH keys stored in the local database. Requires dev mode enabled.",
)
def list_keys():
    if not dev_mode:
        print("Developer mode is not enabled.")
        raise typer.Exit()
    keys = SSHKeyPair.find()
    if not keys:
        print("No SSH keys found")
        return

    print("SSH Keys:")
    for key in keys:
        print(f"Device Name: {key.name}, Public Key: {key.public_key}")


# END Dev mode commands

if __name__ == "__main__":
    app()
