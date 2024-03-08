from __future__ import annotations
from typing import Dict, Any
import json

from pydantic import BaseModel

from agentdesk.processors.grid import GridProcessor


class Action(BaseModel):
    """An action"""

    name: str
    parameters: Dict[str, Any]


class ActionSelection(BaseModel):
    """An action selection from the model"""

    reason: str
    action: Action


def system_prompt(
    actions: Dict[str, Any],
    screen_size: Dict[str, int],
    max_steps: int = 5,
    grid: bool = True,
) -> str:
    """Generate the system prompt

    Args:
        actions (Dict[str, Any]): Actions to select from
        screen_size (Dict[str, int]): Size of the screen (w, h)
        max_steps (int, optional): Max steps. Defaults to 5.
        grid (bool): Whether the image has a grid overlay. Defaults to True

    Returns:
        str: The system prompt
    """
    acts = json.dumps(actions, indent=4)

    query = """You are using a computer, you have access to a mouse and keyboard.
I'm going to show you the picture of the screen along with the current mouse coordinates."""

    if grid:
        query += (
            "I will also give you another picture of the screen with a grid overlaying it where each square is 100px by 100px,"
            "the coordinates of each line intersection are written below it. You can use that to better guage how to move."
        )

    query += f"""
The screen size is ({screen_size["x"]}, {screen_size["y"]})

We will then select from a set of actions:

"""
    query += acts
    query += """

You will return the action in the form of:
{
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "properties": {
        "reason": {
            "type": "string"
        },
        "action": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string"
                },
                "parameters": {
                    "type": "object",
                    "additionalProperties": true
                }
            },
            "required": ["name", "parameters"]
        }
    },
    "required": ["reason", "function"]
}

For example, if we need to move to a search bar located at (400, 500) you would return:
{
    "reason": "I need to move the mouse to be over the seach bar",
    "action": {
        "name": "move_mouse_to",
        "parameters": {"x": 400, "y": 500},
    },
}

If the task is finished, please return the action name 'return', with the parameters of any output that may be needed from the task.

Please be concise and return just the raw valid JSON, the output should be directly parsable as JSON

Okay, when you are ready I'll send you the current screenshot and mouse coordinates.
"""
    return query


def action_prompt(
    task: str,
    screenshot_b64: str,
    x: int,
    y: int,
    screen_size: Dict[str, int],
    grid: bool = True,
) -> dict:
    """Generate an action prompt

    Args:
        task (str): Task to generate the prompt for
        screenshot_b64 (str): b64 encoded screenshot
        x (int): The X coordinate of the mouse
        y (int): They Y coordinate of the mouse
        screen_size (Dict[str, int]): The (w, h) screen size.
        grid (bool): Whether the image has a grid overlay. Defaults to True.

    Returns:
        dict: An openai formatted message
    """
    if grid:
        gp = GridProcessor()
        screenshot_b64_grid = gp.process_b64(screenshot_b64)

    msg = {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": (
                    f"Current mouse coordinates are ({x}, {y}), the screen size is ({screen_size['x']}, {screen_size['y']})"
                    f"and the task to solve is '{task}', please return the appropriate next action as raw JSON. Please review your "
                    "last action carefully and see if the current screenshot reflects what you hoped to accomplish, is the cursor in the right"
                    " location? Does the screen look correct?"
                ),
            },
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{screenshot_b64}"},
            },
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{screenshot_b64_grid}"},
            },
        ],
    }
    return msg
