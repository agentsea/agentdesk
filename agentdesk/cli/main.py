from typing import Optional
import typer
import shutil
import os

from tabulate import tabulate
from namesgenerator import get_random_name

from agentdesk.server.models import V1ProviderData
from agentdesk.vm.load import load_provider
from agentdesk.vm import DesktopVM
from agentdesk.util import convert_unix_to_datetime

app = typer.Typer(no_args_is_help=True)


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
    ssh_key: Optional[str] = typer.Option(
        None, help="The SSH key for the desktop. Optional."
    ),
):
    if not name:
        name = get_random_name(sep="-")

    data = V1ProviderData(type=provider)
    _provider = load_provider(data)

    print(f"Creating desktop '{name}' using '{provider}' provider")
    try:
        _provider.create(name, image, memory, cpu, disk, reserve_ip, ssh_key)
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
                    convert_unix_to_datetime(desktop.created),
                    desktop.provider.type,
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

    if not desktop.reserved_ip:
        print("refreshing provider...")
        _provider = load_provider(desktop.provider)
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

    if not desktop.reserved_ip:
        print("refreshing provider...")
        _provider = load_provider(desktop.provider)
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
    dir = os.path.expanduser("~/.agentsea/")
    shutil.rmtree(dir)

    print(f"cleared cache in {dir}")


if __name__ == "__main__":
    app()
