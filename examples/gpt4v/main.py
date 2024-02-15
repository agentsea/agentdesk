import json
import os

from agentdesk import SimpleDesktop
from examples.gpt4v.agent import solve_task

# Defaine the task
task = "Search for types of ducks in France"
print("solving task: ", task)

# Find or create a local desktop with the simplified action space
desktop = SimpleDesktop.ensure("gpt4v-demo")

# View the desktop, we'll run in the background so it doesn't block
desktop.view(background=True)

# Call our simple agent to solve the task
print("running agent loop...")
result = solve_task(task, "https://google.com", desktop, 5)

print("Result from solving task: ", result)
