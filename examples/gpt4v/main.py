from .instruct import system_prompt
from .oai import call_openai, encode_image
from agentdesk import Desktop


# Create a local desktop
desktop = Desktop.local()

# Get the actions a model can take on the desktop as json schema
actions = desktop.json_schema()

# Create the system prompt
payload = {
    "model": "gpt-4-vision-preview",
    "messages": [
        {
            "role": "system",
            "content": [{"type": "text", "text": system_prompt(actions)}],
        }
    ],
    "max_tokens": 300,
}

response = call_openai(payload)


# Path to your image
image_path = "path_to_your_image.jpg"

# Getting the base64 string
base64_image = encode_image(image_path)

payload = {
    "model": "gpt-4-vision-preview",
    "messages": [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "What’s in this image?"},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"},
                },
            ],
        }
    ],
    "max_tokens": 300,
}


print(response.json())
