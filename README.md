# AgentDesk

A desktop for AI agents.

Built on [agentd](https://github.com/AgentSea/agentd) to make desktop VMs accessible to AI agents.

Implements the [ToolsV1 protocol](https://github.com/AgentSea/agent-tools)

## Installation

```
pip install agentdesk
```

## Usage

Create a local desktop

```python
from agentdesk import Desktop

desktop = Desktop.local()

desktop.move_mouse(500, 500)
```

```bash
agentdesk create --provider qemu
```

Create a remote desktop on GCE

```python
desktop = Desktop.gce()

desktop.click()
```

```bash
agentdesk create --provider gce
```

Create a remote desktop on EC2

```python
desktop = Desktop.ec2()

img = desktop.take_screenshot()
```

```bash
agentdesk create --provider ec2
```

View the desktop in the UI

```python
desktop.view()
```

```bash
agentdesk view old_mckinny --provider ec2
```

_\*requires docker_

### GPT-4V

---

Use with langchain TODO  
Use with baby-agi TODO  
Use with agentsea TODO
