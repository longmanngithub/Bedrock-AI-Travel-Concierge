"""Retry an individual CrewAI model call when Groq's minute quota fills."""
from __future__ import annotations

import re
import time

from crewai import LLM

from .errors import ErrorCode, classify_exception
from .llm import extract_json, rate_limit_retry_delay


class GroqCrewLLM(LLM):
    def __init__(self, model: str, *, quota_retries: int, **kwargs):
        super().__init__(model=model, **kwargs)
        self.quota_retries = max(0, quota_retries)

    def call(self, *args, response_model=None, **kwargs):
        parse_json = response_model is not None and self.response_format == {"type": "json_object"}
        if response_model is not None and not parse_json:
            kwargs["response_model"] = response_model
        for attempt in range(self.quota_retries + 1):
            try:
                result = super().call(*args, **kwargs)
                # CrewAI's Instructor adapter requests a forced tool call
                # when given a response model. JSON mode can validate locally.
                return response_model.model_validate(extract_json(result)) if parse_json else result
            except Exception as exc:
                if attempt == self.quota_retries or classify_exception(exc).code != ErrorCode.E_LLM_RATE_LIMITED:
                    raise
                detail = str(exc)
                tokens = re.search(r"\bLimit (\d+).*?\bRequested (\d+)", detail, re.DOTALL)
                if "per day" in detail.lower() or (tokens and int(tokens[2]) > int(tokens[1])):
                    raise
                time.sleep(rate_limit_retry_delay(exc, attempt))
