import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi.testclient import TestClient

from app import conversation, main
from app.auth.cookies import CSRF_COOKIE


class ChatStreamTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)
        self.enterContext(patch.object(main.limiter, "enabled", False))
        self.client.cookies.set(CSRF_COOKIE, "test-token")
        self.enterContext(patch.dict(
            main.app.dependency_overrides, {main.current_user: lambda: SimpleNamespace()}
        ))
        self.model = Mock(model="groq/openai/gpt-oss-20b")
        self.enterContext(patch.object(conversation, "build_llm", return_value=self.model))
        self.voice = self.enterContext(patch.object(main, "stream_call", return_value=iter(())))
        self.enqueue = self.enterContext(patch.object(
            main, "_enqueue_plan", AsyncMock(return_value={"kind": "job", "job_id": "test-job"})
        ))
        self.enterContext(patch.object(main, "_persist_completed_reply", AsyncMock(return_value=None)))

    def send(self, turns):
        response = self.client.post(
            "/chat/stream",
            json={"messages": [{"role": role, "content": content} for role, content in turns]},
            headers={"X-CSRF-Token": "test-token"},
        )
        self.assertEqual(response.status_code, 200)
        return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]

    def test_budget_turn_reaches_planning_with_nullable_optional_fields(self):
        self.model.call.return_value = json.dumps({
            "slots": {"destination": "Bangkok", "start_date": "2027-01-06",
                      "duration_days": 3, "budget": 1500, "interests": None},
            "missing_required": None, "quick_reply_options": None,
            "ready_to_plan": True,
        })
        frames = self.send([
            ("user", "A three-day Bangkok trip in January"),
            ("assistant", "How much would you like to spend overall?"),
            ("user", "Around $1,500"),
        ])
        self.assertEqual(frames[-1]["kind"], "job")
        self.assertEqual(self.enqueue.call_args.args[1].slots.budget, 1500)
        self.voice.assert_not_called()

    def test_malformed_model_output_is_retryable_without_bad_suggestions(self):
        self.model.call.return_value = "This is not JSON."
        frames = self.send([("user", "I want a street-food trip to Bangkok")])
        self.assertEqual(frames[-1]["code"], "E_EXTRACTION_FAILED")
        self.assertTrue(frames[-1]["retryable"])
        self.voice.assert_not_called()

    def test_groq_conversational_reply_forwards_separate_model_chunks(self):
        self.model.call.return_value = json.dumps({
            "on_topic": True,
            "assistant_reply": "What would you like to explore?",
        })
        self.voice.return_value = iter(("Let's explore", " Bangkok."))
        with patch.object(main, "model_for", return_value="groq/openai/gpt-oss-20b"):
            frames = self.send([("user", "Hi, can you help me plan a trip?")])
        self.voice.assert_called_once()
        self.assertEqual([f["text"] for f in frames if f["kind"] == "token"], [
            "Let's explore", " Bangkok.",
        ])
        self.assertEqual(frames[-1]["message"], "Let's explore Bangkok.")

    def test_budget_answer_survives_model_using_the_wrong_budget_key(self):
        self.model.call.return_value = json.dumps({
            "slots": {"destination": "Bangkok", "start_date": "2027-01-06",
                      "duration_days": 3, "numeric_budget": 1500},
            "missing_required": ["budget", "end_date"],
            "ready_to_plan": False,
        })
        frames = self.send([
            ("user", "A three-day Bangkok trip in January"),
            ("assistant", "How much would you like to spend overall?"),
            ("user", "Around $1,500"),
        ])
        self.assertEqual(frames[-1]["kind"], "job")
        self.assertEqual(self.enqueue.call_args.args[1].slots.budget, 1500)
