import os

import requests

api_key = os.getenv("OPENAI_API_KEY")
if api_key is None:
    raise SystemError("OpenAI API Key not found.")


def chat(msgs: list) -> dict:
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    payload = {
        "model": "gpt-4-vision-preview",
        "messages": msgs,
        "max_tokens": 500,
    }

    print("making request: ", payload)
    response = requests.post(
        "https://api.openai.com/v1/chat/completions", headers=headers, json=payload
    )
    response.raise_for_status()
    print("response: ", response)
    print("response text: ", response.text)

    return response.json()["choices"][0]["message"]
