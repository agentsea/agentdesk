from typing import Optional
import typer
from datetime import datetime

from tabulate import tabulate
from namesgenerator import get_random_name

from agentdesk.server.models import V1ProviderData
from agentdesk.vm.load import load_provider

app = typer.Typer(no_args_is_help=True)


def convert_unix_to_datetime(unix_timestamp: int) -> str:
    # Convert Unix timestamp to datetime
    dt = datetime.utcfromtimestamp(unix_timestamp)
    # Format datetime in a friendly format, e.g., "YYYY-MM-DD HH:MM:SS"
    friendly_format = dt.strftime("%Y-%m-%d %H:%M:%S")
    return friendly_format


@app.command(help="Create a desktop.")
def create(
    name: Optional[str] = typer.Option(
        None, help="The name of the desktop to create. Defaults to a generated name."
    ),
    provider: str = typer.Option(
        "qemu", help="The provider type for the desktop. Defaults to 'qemu'."
    ),
    image: Optional[str] = typer.Option(
        None, help="The image to use for the desktop. Defaults to Ubuntu Jammy."
    ),
    memory: int = typer.Option(
        4, help="The amount of memory (in GB) for the desktop. Defaults to 4."
    ),
    cpu: int = typer.Option(
        2, help="The number of CPU cores for the desktop. Defaults to 2."
    ),
    disk: str = typer.Option(
        "30gb",
        help="The disk size for the desktop. Format as '<size>gb'. Defaults to '30gb'.",
    ),
    reserve_ip: bool = typer.Option(
        False,
        help="Whether to reserve an IP address for the desktop. Defaults to False.",
    ),
    ssh_key: Optional[str] = typer.Option(
        None, help="The SSH key for the desktop. Optional."
    ),
):
    if not name:
        name = get_random_name()

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
    provider: str = typer.Option(
        "qemu", help="The provider type for the desktop. Defaults to 'qemu'."
    ),
):
    data = V1ProviderData(type=provider)
    _provider = load_provider(data)

    if name:
        desktop = _provider.get(name)
        if desktop:
            print(desktop.to_v1_schema().model_dump_json(indent=2))
        else:
            print(f"Desktop '{name}' not found")
        return

    vms = _provider.list()
    if not vms:
        print("No desktops found")
    else:
        table = []
        for desktop in vms:
            table.append(
                [
                    desktop.name,
                    desktop.addr,
                    desktop.status,
                    convert_unix_to_datetime(desktop.created),
                    desktop.provider.type,
                ]
            )

        print(
            tabulate(
                table, headers=["Name", "Address", "Status", "Created", "Provider"]
            )
        )


@app.command(help="Delete a desktop.")
def delete(
    name: str = typer.Argument(..., help="The name of the desktop to delete."),
    provider: str = typer.Option(
        "qemu", help="The provider type for the desktop. Defaults to 'qemu'."
    ),
):
    data = V1ProviderData(type=provider)
    _provider = load_provider(data)

    print(f"Deleting '{name}' desktop...")
    _provider.delete(name)
    print(f"Desktop '{name}' successfully deleted")


@app.command(help="View a desktop in a browser.")
def view(
    name: str = typer.Argument(..., help="The name of the desktop to view."),
    provider: str = typer.Option(
        "qemu", help="The provider type for the desktop. Defaults to 'qemu'."
    ),
):
    data = V1ProviderData(type=provider)
    _provider = load_provider(data)

    desktop = _provider.get(name)
    if not desktop:
        print(f"Desktop '{name}' not found")
        return

    desktop.view()


if __name__ == "__main__":
    app()
