"""Bound Cloudflare structured review across the initial request and one retry."""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import httpx
from crewai import LLM
from pydantic import BaseModel

from .errors import AppError, ErrorCode
from .llm import extract_json


class CloudflareReviewLLM(LLM):
    def call(self, messages, *args, response_model=None, **kwargs):
        if response_model is None:
            return super().call(messages, *args, **kwargs)
        return asyncio.run(self._review(messages, response_model))

    async def acall(self, messages, *args, response_model=None, **kwargs):
        if response_model is None:
            return await super().acall(messages, *args, **kwargs)
        return await self._review(messages, response_model)

    async def _review(self, messages, response_model: type[BaseModel]) -> str:
        account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID")
        api_key = os.getenv("CLOUDFLARE_API_KEY")
        if not account_id or not api_key:
            raise AppError(ErrorCode.E_LLM_UNAVAILABLE, log_detail="Cloudflare credentials missing")

        api_base = os.getenv("CLOUDFLARE_API_BASE") or (
            f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1"
        )
        name = response_model.__name__
        schema = response_model.model_json_schema()
        if isinstance(messages, str):
            messages = [{"role": "user", "content": messages}]
        body: dict[str, Any] = {
            "model": self.model.removeprefix("cloudflare/"),
            "messages": [{"role": message["role"], "content": message["content"]} for message in messages],
            "temperature": self.temperature,
        }
        # Cloudflare GPT-OSS returned text on forced tool calls during the
        # Bangkok replay. Ask for JSON directly and enforce the same schema.
        json_response = self.model.startswith("cloudflare/@cf/openai/gpt-oss-")
        if json_response:
            body["messages"].insert(0, {
                "role": "system",
                "content": "Return only a JSON object matching this schema: " + json.dumps(schema),
            })
            body["reasoning_effort"] = "low"
            # The provider's implicit cap can end the response during reasoning.
            body["max_completion_tokens"] = self.max_tokens or 6000
        else:
            body["tools"] = [{"type": "function", "function": {"name": name, "parameters": schema}}]
            body["tool_choice"] = {"type": "function", "function": {"name": name}}
        loop = asyncio.get_running_loop()
        # Even if the general model timeout is disabled, keep a review bounded.
        deadline = loop.time() + (self.timeout or 120.0)
        try:
            async with asyncio.timeout_at(deadline), httpx.AsyncClient(
                headers={"Authorization": f"Bearer {api_key}"},
            ) as client:
                for attempt in range(2):
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        raise TimeoutError
                    try:
                        response = await client.post(
                            f"{api_base.rstrip('/')}/chat/completions", json=body, timeout=remaining,
                        )
                        response.raise_for_status()
                        message = response.json()["choices"][0]["message"]
                        if json_response:
                            content = message["content"]
                            if not isinstance(content, str):
                                raise ValueError("Expected JSON text from reviewer")
                            return response_model.model_validate(extract_json(content)).model_dump_json()
                        result = message["tool_calls"]
                        function = result[0]["function"]
                        if len(result) != 1 or function["name"] != name:
                            raise ValueError("Expected a single itinerary tool result")
                        return response_model.model_validate_json(function["arguments"]).model_dump_json()
                    except httpx.HTTPStatusError as exc:
                        status = exc.response.status_code
                        if status == 429:
                            raise AppError(ErrorCode.E_LLM_RATE_LIMITED) from exc
                        if attempt or (status != 408 and status < 500):
                            code = ErrorCode.E_LLM_TIMEOUT if status in {408, 504} else ErrorCode.E_LLM_UNAVAILABLE
                            raise AppError(code, log_detail=f"Cloudflare review HTTP {status}") from exc
                    except httpx.TransportError as exc:
                        if attempt:
                            code = ErrorCode.E_LLM_TIMEOUT if isinstance(exc, httpx.TimeoutException) else ErrorCode.E_LLM_UNAVAILABLE
                            raise AppError(code, log_detail="Cloudflare review transport failed") from exc
                    except (ValueError, KeyError, IndexError, TypeError) as exc:
                        if attempt:
                            raise AppError(ErrorCode.E_CREW_FAILED, log_detail="Cloudflare review returned invalid structured output") from exc
        except TimeoutError as exc:
            raise AppError(ErrorCode.E_LLM_TIMEOUT, log_detail="Cloudflare review deadline expired") from exc
        raise AssertionError("Cloudflare review retry loop did not settle")
