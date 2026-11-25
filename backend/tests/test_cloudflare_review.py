import asyncio
import json
import os
import unittest
from unittest.mock import patch

import httpx

from app.cloudflare_llm import CloudflareReviewLLM
from app.errors import AppError, ErrorCode
from app.schemas import Itinerary


class CloudflareReviewTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        env = patch.dict(os.environ, {
            "CLOUDFLARE_ACCOUNT_ID": "test-account", "CLOUDFLARE_API_KEY": "test-token",
        })
        env.start()
        self.addCleanup(env.stop)

    async def test_gpt_oss_validates_json_without_forcing_a_tool_call(self):
        reviewer = CloudflareReviewLLM(model="cloudflare/@cf/openai/gpt-oss-120b", timeout=5)

        async def respond(client, request, **kwargs):
            body = json.loads(request.content)
            self.assertNotIn("tools", body)
            self.assertEqual(body["reasoning_effort"], "low")
            self.assertEqual(body["max_completion_tokens"], 6000)
            self.assertIn("daily_plans", body["messages"][0]["content"])
            return httpx.Response(200, request=request, json={"choices": [{"message": {
                "content": '```json\n{"destination":"Bangkok"}\n```',
            }}]})

        with patch.object(httpx.AsyncClient, "send", respond):
            result = await reviewer.acall("Review the draft", response_model=Itinerary)
        self.assertEqual(Itinerary.model_validate_json(result).destination, "Bangkok")

    async def test_invalid_response_is_retried_once_with_remaining_budget(self):
        reviewer = CloudflareReviewLLM(model="cloudflare/@cf/zai-org/glm-4.7-flash", timeout=2)
        timeouts = []

        async def respond(client, request, **kwargs):
            timeouts.append(request.extensions["timeout"]["read"])
            if len(timeouts) == 1:
                return httpx.Response(200, text="<html>temporary upstream error</html>", request=request)
            return httpx.Response(200, request=request, json={"choices": [{"message": {"tool_calls": [
                {"function": {"name": "Itinerary", "arguments": '{"destination":"Bangkok"}'}}
            ]}}]})

        with patch.object(httpx.AsyncClient, "send", respond):
            result = await reviewer.acall("Review the draft", response_model=Itinerary)
        self.assertEqual(Itinerary.model_validate_json(result).destination, "Bangkok")
        self.assertEqual(len(timeouts), 2)
        self.assertLess(timeouts[1], timeouts[0])

    async def test_request_and_retry_share_a_review_deadline(self):
        reviewer = CloudflareReviewLLM(model="cloudflare/@cf/zai-org/glm-4.7-flash", timeout=0.2)
        calls = 0

        async def respond(client, request, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return httpx.Response(502, request=request)
            await asyncio.sleep(5)
            self.fail("The review exceeded its deadline")

        with patch.object(httpx.AsyncClient, "send", respond), self.assertRaises(AppError) as error:
            await reviewer.acall("Review the draft", response_model=Itinerary)
        self.assertEqual(error.exception.code, ErrorCode.E_LLM_TIMEOUT)
        self.assertEqual(calls, 2)
