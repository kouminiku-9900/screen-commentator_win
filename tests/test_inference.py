from __future__ import annotations

import json

import httpx

from screen_commentator_win.inference import OpenAICompatibleInferenceClient


def test_inference_client_sends_multimodal_json_schema_request() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["payload"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"comments":["ここすき"],"mood":"general","excitement":5}'
                        }
                    }
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport)
    client = OpenAICompatibleInferenceClient(
        base_url="http://127.0.0.1:12346",
        instance_id="screen-commentator-vlm",
        timeout_sec=12.0,
        http_client=http_client,
    )

    batch = client.generate_comments(prompt="prompt", image_base64="QUJD")

    payload = captured["payload"]
    assert captured["url"] == "http://127.0.0.1:12346/v1/chat/completions"
    assert isinstance(payload, dict)
    assert payload["model"] == "screen-commentator-vlm"
    assert payload["response_format"]["type"] == "json_schema"
    assert payload["messages"][0]["content"][1]["image_url"]["url"] == "data:image/jpeg;base64,QUJD"
    assert batch.comments == ["ここすき"]
