import argparse
import logging

from agentdesk import SimpleDesktop
from .agent import solve_task

parser = argparse.ArgumentParser(description="Run the agent with optional debug mode.")
parser.add_argument(
    "--debug",
    action="store_true",
    help="Enable debug mode for more verbose output.",
    default=False,
)
args = parser.parse_args()

if args.debug:
    logging.basicConfig(level=logging.DEBUG)
else:
    logging.basicConfig(level=logging.INFO)

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
