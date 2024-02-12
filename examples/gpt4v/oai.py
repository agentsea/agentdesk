import os
import base64

import requests

api_key = os.getenv("OPENAI_API_KEY")
if api_key is None:
    raise SystemError("OpenAI API Key not found.")


def chat(msgs: list):
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    payload = {
        "model": "gpt-4-vision-preview",
        "messages": msgs,
    }

    response = requests.post(
        "https://api.openai.com/v1/chat/completions", headers=headers, json=payload
    )

    return response.json()["choices"][0]["message"]
