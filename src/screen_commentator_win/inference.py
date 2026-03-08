from __future__ import annotations

import httpx

from .comment_parser import parse_comment_batch
from .contracts import InferenceClient
from .models import CommentBatch


RESPONSE_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "screen_comment_batch",
        "schema": {
            "type": "object",
            "properties": {
                "comments": {"type": "array", "items": {"type": "string"}},
                "mood": {
                    "type": "string",
                    "enum": [
                        "excitement",
                        "funny",
                        "surprise",
                        "cute",
                        "boring",
                        "beautiful",
                        "general",
                    ],
                },
                "excitement": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            "required": ["comments", "mood", "excitement"],
            "additionalProperties": False,
        },
    },
}


class OpenAICompatibleInferenceClient(InferenceClient):
    def __init__(
        self,
        base_url: str,
        instance_id: str,
        timeout_sec: float,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.instance_id = instance_id
        self.timeout_sec = timeout_sec
        self.http_client = http_client or httpx.Client()

    def generate_comments(self, prompt: str, image_base64: str) -> CommentBatch:
        response = self.http_client.post(
            f"{self.base_url}/v1/chat/completions",
            timeout=self.timeout_sec,
            json={
                "model": self.instance_id,
                "temperature": 0.7,
                "top_p": 0.8,
                "max_tokens": 512,
                "stream": False,
                "response_format": RESPONSE_SCHEMA,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"},
                            },
                        ],
                    }
                ],
            },
        )
        response.raise_for_status()
        payload = response.json()
        content = payload["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(item.get("text", "") for item in content if isinstance(item, dict))
        return parse_comment_batch(str(content))
