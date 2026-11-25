import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from app.config import Settings
from app.crew.agents import build_agents
from app.llm import build_llm
from app.routing import _parse_task_tiers, _tier_models
from app.schemas import Itinerary


class MixedProviderTests(unittest.TestCase):
    def test_cloudflare_review_uses_scoped_dotenv_credentials_and_groq_handles_chat(self):
        for cache in (_parse_task_tiers, _tier_models):
            cache.cache_clear()
            self.addCleanup(cache.cache_clear)

        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, clear=True):
            env_file = Path(temporary) / ".env"
            env_file.write_text(
                "MODEL=groq/openai/gpt-oss-20b\n"
                "TIER_QUALITY=cloudflare/@cf/zai-org/glm-4.7-flash\n"
                "CLOUDFLARE_ACCOUNT_ID=test-account\n"
                "CLOUDFLARE_API_KEY=test-token\n"
                "REQUEST_TIMEOUT=40\n"
            )
            settings = Settings(_env_file=env_file)
            provider_requests = []

            def respond(client, request, **kwargs):
                self.assertEqual(
                    str(request.url),
                    "https://api.cloudflare.com/client/v4/accounts/test-account/ai/v1/chat/completions",
                )
                self.assertEqual(request.headers["authorization"], "Bearer test-token")
                body = json.loads(request.content)
                self.assertEqual(body["model"], "@cf/zai-org/glm-4.7-flash")
                provider_requests.append(body)
                message = {"role": "assistant", "content": "Draft ready"}
                finish = "stop"
                if body.get("tools"):
                    self.assertLessEqual(request.extensions["timeout"]["read"], 40)
                    message = {
                        "role": "assistant", "content": None,
                        "tool_calls": [{
                            "id": "call_test", "type": "function",
                            "function": {
                                "name": body["tools"][0]["function"]["name"],
                                "arguments": '{"destination":"Bangkok"}',
                            },
                        }],
                    }
                    finish = "tool_calls"
                return httpx.Response(200, request=request, json={
                    "id": "test-response", "created": 1, "object": "chat.completion",
                    "model": body["model"],
                    "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                })

            async def respond_async(client, request, **kwargs):
                return respond(client, request, **kwargs)

            with (
                patch("app.llm.get_settings", return_value=settings),
                patch("app.routing.get_settings", return_value=settings),
                patch.object(httpx.Client, "send", respond),
                patch.object(httpx.AsyncClient, "send", respond_async),
            ):
                agents = build_agents(rag_enabled=False)
                self.assertEqual(build_llm(task="extraction").model, "groq/openai/gpt-oss-20b")
                self.assertEqual(agents["food"].llm.model, "groq/openai/gpt-oss-20b")
                self.assertEqual(agents["planner"].llm.call("Draft a plan"), "Draft ready")
                review = agents["reviewer"].llm.call("Review the draft", response_model=Itinerary)
                self.assertEqual(Itinerary.model_validate_json(review).destination, "Bangkok")
            self.assertEqual(len(provider_requests), 2)
            self.assertTrue(provider_requests[-1]["tools"])
