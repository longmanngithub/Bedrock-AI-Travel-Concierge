import unittest
from unittest.mock import Mock, patch

from app.errors import AppError, ErrorCode
from app.llm import call_with_retry


class GroqRateLimitError(RuntimeError):
    status_code = 429


class LLMRetryTests(unittest.TestCase):
    @patch("app.llm.time.sleep")
    def test_groq_daily_token_limit_fails_without_backoff(self, sleep):
        llm = Mock(model="groq/openai/gpt-oss-20b")
        llm.call.side_effect = GroqRateLimitError("Rate limit reached on tokens per day (TPD).")

        with self.assertRaises(AppError) as error:
            call_with_retry(llm, "Return JSON")

        self.assertEqual(error.exception.code, ErrorCode.E_LLM_RATE_LIMITED)
        llm.call.assert_called_once()
        sleep.assert_not_called()

    @patch("app.llm.time.sleep")
    def test_retries_groq_token_quota_with_provider_delay(self, sleep):
        llm = Mock()
        error = GroqRateLimitError("Rate limit reached on tokens per minute. Please try again in 7.25s.")
        llm.call.side_effect = [error, '{"budget": 1000}']

        self.assertEqual(call_with_retry(llm, "$1000"), '{"budget": 1000}')
        sleep.assert_called_once_with(8.25)
        self.assertEqual(llm.call.call_count, 2)

    @patch("app.llm.time.sleep")
    def test_reports_groq_rate_limit_when_attempts_run_out(self, sleep):
        llm = Mock()
        llm.call.side_effect = GroqRateLimitError("Token quota exhausted")

        with self.assertRaises(AppError) as error:
            call_with_retry(llm, "$1000", max_retries=1)

        self.assertEqual(error.exception.code, ErrorCode.E_LLM_RATE_LIMITED)
        sleep.assert_not_called()


class CrewQuotaRetryTests(unittest.TestCase):
    def test_reviewer_validates_json_without_forcing_a_groq_tool_call(self):
        from crewai import LLM
        from app.groq_llm import GroqCrewLLM
        from app.schemas import Itinerary

        model = GroqCrewLLM(
            model="groq/openai/gpt-oss-20b", quota_retries=0,
            response_format={"type": "json_object"},
        )
        with patch.object(LLM, "call", return_value='{"destination": "Bangkok"}') as provider:
            self.assertEqual(model.call("Review this draft", response_model=Itinerary).destination, "Bangkok")
        provider.assert_called_once_with("Review this draft")

    def test_a_crew_call_retries_the_same_step_after_a_minute_limit(self):
        from crewai import LLM
        from app.groq_llm import GroqCrewLLM

        model = GroqCrewLLM(model="groq/openai/gpt-oss-20b", quota_retries=2)
        error = GroqRateLimitError("Rate limit reached. Please try again in 7.25s.")
        with (
            patch.object(LLM, "call", side_effect=[error, "step completed"]),
            patch("app.groq_llm.time.sleep") as sleep,
        ):
            self.assertEqual(model.call("finish the current task"), "step completed")
        sleep.assert_called_once_with(8.25)

    def test_a_crew_request_larger_than_the_quota_fails_without_waiting(self):
        from crewai import LLM
        from app.groq_llm import GroqCrewLLM

        model = GroqCrewLLM(model="groq/openai/gpt-oss-20b", quota_retries=2)
        with (
            patch.object(LLM, "call", side_effect=GroqRateLimitError("Limit 8000, Requested 9000")),
            patch("app.groq_llm.time.sleep") as sleep,
            self.assertRaises(GroqRateLimitError),
        ):
            model.call("finish the current task")
        sleep.assert_not_called()
