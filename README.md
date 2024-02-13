# AgentDesk

A desktop for AI agents.

Built on [agentd](https://github.com/AgentSea/agentd) to make desktop VMs accessible to AI agents.

Implements the [ToolsV1 protocol](https://github.com/AgentSea/agent-tools)

## Installation

```
pip install agentdesk
```

## Quick Start

```python
from agentdesk import Desktop

desktop = Desktop.local()
desktop.view(background=True)

desktop.open_url("https://google.com")

desktop.move_mouse(500, 500)

desktop.click()

img = desktop.take_screenshot()
```

## Usage

### Create a local desktop

```python
from agentdesk import Desktop

desktop = Desktop.local()
```

```bash
$ agentdesk create --provider qemu
```

_\*requires [qemu](https://www.qemu.org/)_

### Create a remote desktop on GCE

```python
desktop = Desktop.gce()
```

```bash
$ agentdesk create --provider gce
```

### Create a remote desktop on EC2

```python
desktop = Desktop.ec2()
```

```bash
$ agentdesk create --provider ec2
```

### View the desktop in the UI

```python
desktop.view()
```

```bash
$ agentdesk view old_mckinny
```

_\*requires docker_

### List desktops

```python
Desktop.list()
```

```bash
$ agentdesk get
```

### Delete a desktop

```python
Desktop.delete("old_mckinny")
```

```bash
$ agentdesk delete old_mckinny
```

### Use the desktop

```python
desktop.open_url("https://google.com")

coords = desktop.mouse_coordinates()

desktop.move_mouse(500, 500)

desktop.click()

desktop.type_text("What kind of ducks are in Canada?")

desktop.press_key('Enter')

desktop.scroll()

img = desktop.take_screenshot()
```

### Examples

[GPT-4V](./examples/gpt4v/note.ipynb) provides a notebook using a desktop

## Developing

Please open an issue before creating a PR.
