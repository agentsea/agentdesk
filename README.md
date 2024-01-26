# Agent Desk

A desktop for AI agents.

Built on [agentd](https://github.com/AgentSea/agentd) to make desktop VMs accessible to AI agents.

Implements the [AgentToolsV1 protocol](https://github.com/AgentSea/agent-tools)

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

Create a remote desktop on GCE

```python
desktop = Desktop.gce()

desktop.click()
```

Create a remote desktop on EC2

```python
desktop = Desktop.ec2()

img = desktop.take_screenshot()
```

View the desktop in the UI

```python
desktop.view()
```

_\*requires docker_

Embed the desktop in a React app

```js
import AgentDesk from "@agentsea/agentdesk";

<AgentDesk addr="foo.bar" />;
```

---

Use with langchain TODO  
Use with baby-agi TODO  
Use with agentsea TODO

Record actions TODO
