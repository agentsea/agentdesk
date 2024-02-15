from typing import Dict, Any, List
import json
import time
import logging
from typing import Final
from copy import deepcopy

from tenacity import (
    retry,
    stop_after_attempt,
    before_sleep_log,
)

from examples.gpt4v.instruct import system_prompt, ActionSelection
from examples.gpt4v.oai import chat
from agentdesk import Desktop
from .instruct import system_prompt, action_prompt, ActionSelection

logger: Final = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


@retry(
    stop=stop_after_attempt(5),
    before_sleep=before_sleep_log(logger, logging.INFO),
)
def take_action(
    desktop: Desktop,
    task: str,
    msgs: List,
) -> bool:
    """Take an action

    Args:
        desktop (Desktop): Desktop to use
        task (str): Task to accomplish
        msgs (List): Messages for the task

    Returns:
        bool: Whether the task is complete
    """
    _msgs = deepcopy(msgs)

    print("\ntaking action with current msg history: ", _msgs)
    screenshot_b64 = desktop.take_screenshot()

    x, y = desktop.mouse_coordinates()
    print("x, y: ", x, y)

    msg = action_prompt(
        task,
        screenshot_b64,
        x,
        y,
    )
    print("msg: ", msg)
    _msgs.append(msg)

    response = chat(_msgs)
    print("gpt response: ", response)

    try:
        jdict = json.loads(response)
        print("jdict: ", jdict)

        selection = ActionSelection(**jdict)
        print("action selection: ", selection)

    except Exception as e:
        print(f"Response failed to parse: {e}")
        raise

    if selection.action.name == "finished":
        print("finished")
        msgs = _msgs
        return True

    action = desktop.find_action(selection.action.name)
    print("found action: ", action)
    if not action:
        print("action returned not found: ", selection.action.name)
        raise SystemError("action not found")

    response = desktop.use(action, **selection.action.parameters)
    msgs = _msgs
    print("used action response: ", response)

    return False


def solve_task(
    task: str,
    base_url: str,
    desktop: Desktop,
    max_steps: int = 5,
) -> List:
    """Solve a task for a site

    Args:
        task (str): Task to solve
        base_url (str): Base URL
        desktop (Desktop): An AgentDesk desktop instance.
        max_steps (int, optional): Max steps to try and solve. Defaults to 5.

    Returns:
        List: The msg history
    """

    print("opening base url: ", base_url)
    desktop.open_url(base_url)
    print("opened url")
    time.sleep(10)

    desktop.move_mouse(500, 500)
    tools = desktop.json_schema()
    print("tools: ", tools)

    sys_prompt = system_prompt(tools)
    print("system prompt: ", sys_prompt)

    response = chat(sys_prompt)
    print("system prompt response: ", response)

    msgs = [response]
    for i in range(max_steps):
        print(f"\nstep {i}")

        print("desktop: ", desktop)
        done = take_action(desktop, task, msgs)
        print("msg history post action: ", msgs)

        if done:
            print("task is done")
            return msgs

        time.sleep(2)
