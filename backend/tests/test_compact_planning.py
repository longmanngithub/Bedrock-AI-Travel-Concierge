import json
import unittest
from unittest.mock import Mock, patch

from app.crew.crew import run_compact_itinerary
from app.errors import AppError, ErrorCode
from app.schemas import TripRequest


class CompactPlanningTests(unittest.TestCase):
    def setUp(self):
        self.trip = TripRequest(
            destination="Bangkok", start_date="2026-12-21", end_date="2026-12-21", budget=1500,
        )
        self.progress = Mock()

    def run_plan(self, payload):
        model = "groq/openai/gpt-oss-20b"
        llm = Mock(model=model)
        llm.call.return_value = json.dumps(payload)
        with patch("app.baseline.build_llm", return_value=llm) as factory:
            result = run_compact_itinerary(
                self.trip, model=model, previous=None, progress=self.progress, traveller_memory=None,
            )
        factory.assert_called_once_with(model=model, task="compact_plan")
        llm.call.assert_called_once()
        self.assertIn("Monday (2026-12-21)", llm.call.call_args.args[0][1]["content"])
        return result[0]

    def test_returns_a_complete_itinerary_with_reconciled_budget_and_no_unverified_citations(self):
        itinerary = self.run_plan({
            "destination": "Bangkok", "num_days": 99,
            "daily_plans": [{"day": 1, "morning": "Temple", "afternoon": "River", "evening": "Market"}],
            "restaurants": [{"name": "Market Stall", "source_ids": ["s1"]}],
            "budget": {"food": 50, "accommodation": 120, "total_estimated": 9999},
        })
        self.assertEqual(itinerary.num_days, 1)
        self.assertEqual(itinerary.budget.total_estimated, 170)
        self.assertTrue(itinerary.within_budget)
        self.assertEqual(itinerary.restaurants[0].source_ids, [])
        self.assertEqual(itinerary.sources, [])
        self.progress.step.assert_any_call("reviewer", "done", "Checked the daily schedule and budget totals")

    def test_truncated_itinerary_returns_a_retryable_error(self):
        with self.assertRaises(AppError) as context:
            self.run_plan({"destination": "Bangkok", "daily_plans": []})
        self.assertEqual(context.exception.code, ErrorCode.E_CREW_FAILED)
        self.assertTrue(context.exception.retryable)
