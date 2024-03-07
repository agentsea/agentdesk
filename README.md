<!-- PROJECT LOGO -->
<br />
<p align="center">
  <!-- <a href="https://github.com/agentsea/skillpacks">
    <img src="https://project-logo.png" alt="Logo" width="80">
  </a> -->

  <h1 align="center">AgentDesk</h1>

  <p align="center">
    Desktops for AI agents &nbsp; :computer:
    <br />
    <a href="https://github.com/agentsea/agentdesk"><strong>Explore the docs »</strong></a>
    <br />
    <br />
    <a href="https://github.com/agentsea/agentdesk">View Demo</a>
    ·
    <a href="https://github.com/agentsea/agentdesk/issues">Report Bug</a>
    ·
    <a href="https://github.com/agentsea/agentdesk/issues">Request Feature</a>
  </p>
  <br>
</p>

Agentdesk provides full featured desktop environments which can be programatically controlled by AI agents. Spin them up locally or in the cloud.

▶ Built on [agentd](https://github.com/agentsea/agentd) a runtime daemon which exposes a REST API for interacting with the desktop.

▶ Implements the [ToolsV1 protocol](https://github.com/agentsea/opentool)

## Installation

```
pip install agentdesk
```

## Quick Start

```python
from agentdesk import Desktop

# Create a local VM
desktop = Desktop.local()

# Launch the UI for it
desktop.view(background=True)

# Open a browser to Google
desktop.open_url("https://google.com")

# Take actions on the desktop
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
Desktop.find()
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

### Processors

Process images to make them more accessible to LMMs.

#### Grid

Add a coordinate grid on top of the image

```python
from agentdesk.processors import GridProcessor

img = desktop.take_screenshot()

processor = GridProcessor()
grid_img = processor.process_b64(img)
```

## Examples

### GPT-4V

See how to use GPT-4V with AgentDesk in our [notebook](./examples/gpt4v/note.ipynb) or [agent](./examples/gpt4v/main.py)

## Developing

Please open an issue before creating a PR.

Changes to the VM happen in [agentd](https://github.com/agentsea/agentd)
