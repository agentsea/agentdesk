from typing import List, Tuple
import json
import time
import logging
from typing import Final
from copy import deepcopy
import pprint

from tenacity import (
    retry,
    stop_after_attempt,
    before_sleep_log,
)

from agentdesk import Desktop
from .oai import chat
from .instruct import system_prompt, action_prompt, ActionSelection
from .util import remove_user_image_urls, clean_llm_json, shorten_user_image_urls

logger: Final = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


@retry(
    stop=stop_after_attempt(5),
    before_sleep=before_sleep_log(logger, logging.INFO),
)
def take_action(
    desktop: Desktop, task: str, msgs: List, screen_size: dict
) -> Tuple[List, bool]:
    """Take an action

    Args:
        desktop (Desktop): Desktop to use
        task (str): Task to accomplish
        msgs (List): Messages for the task
        screen_size (dict): Size of the screen

    Returns:
        bool: Whether the task is complete
    """
    print("taking action...")

    _msgs = deepcopy(msgs)
    _msgs = remove_user_image_urls(_msgs)

    screenshot_b64 = desktop.take_screenshot()

    x, y = desktop.mouse_coordinates()
    print("x, y: ", x, y)

    msg = action_prompt(task, screenshot_b64, x, y, screen_size)
    _msgs.append(msg)

    logging.debug("calling chat with msgs")
    logging.debug(pprint.pprint(shorten_user_image_urls(deepcopy(_msgs))))

    response = chat(_msgs)
    print("\ngpt response: ", response)

    try:
        cleaned_content = clean_llm_json(response["content"])
        jdict = json.loads(cleaned_content)

        selection = ActionSelection(**jdict)
        print("\naction selection: ", selection)

    except Exception as e:
        print(f"Response failed to parse: {e}")
        raise

    if selection.action.name == "return":
        print("\nfinished!")
        _msgs.append(response)
        return _msgs, True

    action = desktop.find_action(selection.action.name)
    print("found action: ", action)
    if not action:
        print("\naction returned not found: ", selection.action.name)
        raise SystemError("action not found")

    try:
        action_response = desktop.use(action, **selection.action.parameters)
    except Exception as e:
        raise ValueError(f"Trouble using action: {e}")

    print("action output: ", action_response)

    _msgs.append(response)
    return _msgs, False


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
    print("waiting for browser to open...")
    time.sleep(15)

    desktop.move_mouse(500, 500)
    tools = desktop.json_schema()
    print("\ntools: ")
    pprint.pprint(tools)

    info = desktop.info()
    screen_size = info["screen_size"]

    msgs = []
    msg = {
        "role": "system",
        "content": [{"type": "text", "text": system_prompt(tools, screen_size)}],
    }
    msgs.append(msg)

    response = chat(msgs)
    print("\nsystem prompt response: ", response)
    msgs.append(response)

    for i in range(max_steps):
        print(f"\n\n-------\n\nstep {i + 1}\n")

        msgs, done = take_action(desktop, task, msgs, screen_size)

        if done:
            print("task is done")
            return msgs

        time.sleep(2)
