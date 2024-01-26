# Agent Desk

A desktop for AI agents.

Built on [agentd](https://github.com/AgentSea/agentd) to make desktop VMs accessible to AI agents.

Implements the [AgentToolsV1 protocol](https://github.com/AgentSea/agent-tools)

## Installation

```
pip install agent-desk
```

## Usage

Create a local desktop

```python
from agent_desk import Desktop

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

---

Use with langchain TODO  
Use with baby-agi TODO  
Use with agentsea TODO

View in the UI TODO  
Record actions TODO
