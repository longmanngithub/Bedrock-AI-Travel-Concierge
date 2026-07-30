"""Tests for the replan confirmation gate and quick-reply context awareness.

These cover the deterministic backstops in conversation.py, not the LLM call
itself: `extract_turn` is exercised with the model response stubbed out, so
each test pins down what the code does with a given (model output,
transcript) pair. The behaviour under test is load-bearing — see the
bidirectional-gate comment in extract_turn for the live failure that motivated
it (a confirmation loop with no exit, which the model papered over by claiming
changes had already been applied).
"""
from __future__ import annotations

import json
import unittest
from unittest import mock

from app import conversation as conv
from app.conversation import (
    ChatTurn,
    ExtractionResult,
    SlotValues,
    _confirmation_offer_count,
    _reads_as_confirmation,
    _validate_quick_replies,
    build_follow_ups,
)

MARKER = "[Itinerary already built for the traveller — record_id: 42]"
OFFER = "Want me to fold that into your itinerary now, or is there anything else you'd like to add first?"


def turns(*pairs: tuple[str, str]) -> list[ChatTurn]:
    return [ChatTurn(role=role, content=content) for role, content in pairs]


def planned_slots(**overrides) -> dict:
    slots = {
        "destination": "Barcelona",
        "budget": 3000,
        "start_date": "2026-09-01",
        "end_date": "2026-09-05",
        "travelers": 2,
        "pace": "balanced",
    }
    slots.update(overrides)
    return slots


def run_extract(model_payload: dict, messages: list[ChatTurn]) -> ExtractionResult:
    """Run extract_turn with the LLM call stubbed to return `model_payload`."""
    with mock.patch.object(conv, "build_llm", return_value=object()), mock.patch.object(
        conv, "call_with_retry", return_value=json.dumps(model_payload)
    ):
        return conv.extract_turn(messages)


class ConfirmationPhrasingTests(unittest.TestCase):
    def test_accepts_plain_affirmations(self) -> None:
        for text in ("yes", "Yeah", "go ahead", "do it", "sounds good", "let's do it"):
            with self.subTest(text=text):
                self.assertTrue(_reads_as_confirmation(text))

    def test_accepts_confirmation_that_does_not_lead_with_yes(self) -> None:
        # The live miss: first word is "No" (declining to add anything else),
        # but the message is unambiguously a confirmation.
        for text in (
            "No, it is confirmed now",
            "I'm ready to apply them",
            "Nothing else, that's confirmed",
        ):
            with self.subTest(text=text):
                self.assertTrue(_reads_as_confirmation(text))

    def test_accepts_explicit_apply_now_instruction(self) -> None:
        for text in (
            "Please add these into the itinerary plan",
            "Please, incorporate that into the itinerary",
            "put that in my itinerary",
            "update the itinerary",
            "apply them",
        ):
            with self.subTest(text=text):
                self.assertTrue(_reads_as_confirmation(text))

    def test_rejects_non_confirmations(self) -> None:
        for text in (
            "",
            "what should I pack?",
            "how do I get from Barcelona to Madrid?",
            "day 3 feels a bit heavy",
            "it is not confirmed",
        ):
            with self.subTest(text=text):
                self.assertFalse(_reads_as_confirmation(text))

    def test_rejects_negated_apply_instructions(self) -> None:
        """The imperative patterns are broad by design, so negation and
        question forms are what keep them from over-firing."""
        for text in (
            "don't apply it yet",
            "not yet confirmed",
            "I do not want to update the itinerary yet",
            "I don't want to add that",
            "no need to update the plan",
            "never apply that",
        ):
            with self.subTest(text=text):
                self.assertFalse(_reads_as_confirmation(text))

    def test_rejects_wh_questions_about_the_itinerary(self) -> None:
        # Asking for suggestions is not granting a go-ahead.
        for text in (
            "what would you add to the itinerary?",
            "which hotels would you add to the plan?",
            "how would you change the itinerary?",
        ):
            with self.subTest(text=text):
                self.assertFalse(_reads_as_confirmation(text))

    def test_polite_question_form_imperative_still_confirms(self) -> None:
        # "Could you add that...?" is a request, not an information question —
        # the wh-word guard must not swallow it.
        for text in (
            "Could you add that to the itinerary?",
            "yes please add it to the itinerary",
            "ok go ahead and apply them",
        ):
            with self.subTest(text=text):
                self.assertTrue(_reads_as_confirmation(text))


class ReplanGateTests(unittest.TestCase):
    def _payload(self, *, ready: bool, special: str | None) -> dict:
        return {
            "on_topic": True,
            "slots": planned_slots(special_requests=special),
            "missing_required": [],
            "ready_to_plan": ready,
            "assistant_reply": "Sure thing.",
            "quick_reply_slot": None,
            "quick_reply_options": [],
        }

    def test_model_false_is_overruled_on_a_clear_confirmation(self) -> None:
        """The regression: a model `false` used to be unappealable."""
        messages = turns(
            ("user", "5 days in Barcelona for 2, food focused, $3000"),
            ("assistant", f"Here you go! {MARKER}"),
            ("user", "Please add packing suggestions to the itinerary"),
            ("assistant", OFFER),
            ("user", "No, it is confirmed now"),
        )
        result = run_extract(
            self._payload(ready=False, special="add packing suggestions"), messages
        )
        self.assertTrue(result.ready_to_plan)
        self.assertEqual(result.route, "revise")

    def test_apply_now_instruction_confirms_itself(self) -> None:
        messages = turns(
            ("user", "5 days in Barcelona for 2"),
            ("assistant", f"Here you go! {MARKER}"),
            ("user", "Please add these into the itinerary plan"),
        )
        result = run_extract(
            self._payload(ready=False, special="add packing suggestions"), messages
        )
        self.assertTrue(result.ready_to_plan)

    def test_still_downgrades_an_unconfirmed_concrete_request(self) -> None:
        """The confirmation gate itself must keep working — a first concrete
        ask that isn't an apply-now instruction still waits for a yes."""
        messages = turns(
            ("user", "5 days in Barcelona for 2"),
            ("assistant", f"Here you go! {MARKER}"),
            ("user", "day 3 feels a bit heavy"),
        )
        result = run_extract(
            self._payload(ready=True, special="lighten day 3"), messages
        )
        self.assertFalse(result.ready_to_plan)
        self.assertEqual(result.route, "answer")

    def test_does_not_replan_when_nothing_is_pending(self) -> None:
        messages = turns(
            ("user", "5 days in Barcelona for 2"),
            ("assistant", f"Here you go! {MARKER}"),
            ("user", "yes"),
        )
        result = run_extract(self._payload(ready=True, special=None), messages)
        self.assertFalse(result.ready_to_plan)

    def test_stall_breaker_fires_after_two_unanswered_offers(self) -> None:
        messages = turns(
            ("user", "5 days in Barcelona for 2"),
            ("assistant", f"Here you go! {MARKER}"),
            ("user", "I'd love some packing tips"),
            ("assistant", OFFER),
            ("user", "sure whatever you think"),
            ("assistant", "Would you like me to add those packing tips?"),
            ("user", "that would be lovely"),
        )
        result = run_extract(
            self._payload(ready=False, special="add packing suggestions"), messages
        )
        self.assertTrue(result.ready_to_plan)

    def test_stall_breaker_respects_an_explicit_not_yet(self) -> None:
        messages = turns(
            ("user", "5 days in Barcelona for 2"),
            ("assistant", f"Here you go! {MARKER}"),
            ("user", "I'd love some packing tips"),
            ("assistant", OFFER),
            ("user", "hmm"),
            ("assistant", "Would you like me to add those packing tips?"),
            ("user", "not yet, let me think"),
        )
        result = run_extract(
            self._payload(ready=False, special="add packing suggestions"), messages
        )
        self.assertFalse(result.ready_to_plan)

    def test_first_plan_needs_no_confirmation(self) -> None:
        """No itinerary yet → the gate must not touch a legitimate first plan."""
        messages = turns(("user", "5 days in Barcelona for 2, food focused, $3000"))
        result = run_extract(self._payload(ready=True, special=None), messages)
        self.assertTrue(result.ready_to_plan)
        self.assertEqual(result.route, "plan")

    def test_confirmation_offers_before_the_itinerary_do_not_count(self) -> None:
        messages = turns(
            ("assistant", "Would you like me to add a few food stops?"),
            ("user", "sure"),
            ("assistant", f"Here you go! {MARKER}"),
        )
        self.assertEqual(_confirmation_offer_count(messages), 0)


class QuickReplyContextTests(unittest.TestCase):
    def _result(self, *, options: list[str], special: str | None = None, **slot_kw) -> ExtractionResult:
        return ExtractionResult(
            on_topic=True,
            slots=SlotValues(special_requests=special, **slot_kw),
            missing_required=[],
            ready_to_plan=False,
            quick_reply_options=options,
        )

    def test_pending_confirmation_gets_yes_no_chips_only(self) -> None:
        messages = turns(("assistant", f"Here you go! {MARKER}"))
        result = self._result(
            options=["What should I pack?", "Help me choose a destination"],
            special="add packing suggestions",
            destination="Barcelona",
        )
        self.assertEqual(_validate_quick_replies(result, messages), conv._CONFIRM_CHIPS)

    def test_drops_chips_for_already_settled_slots(self) -> None:
        messages = turns(("assistant", f"Here you go! {MARKER}"))
        result = self._result(
            options=["Around $3,000", "Next month", "Add food recommendations"],
            destination="Barcelona",
            budget=3000,
            start_date="2026-09-01",
            end_date="2026-09-05",
        )
        self.assertEqual(
            _validate_quick_replies(result, messages), ["Add food recommendations"]
        )

    def test_falls_back_to_post_itinerary_chips_not_destination_picking(self) -> None:
        messages = turns(("assistant", f"Here you go! {MARKER}"))
        result = self._result(
            options=["Around $3,000"],
            destination="Barcelona",
            budget=3000,
        )
        self.assertEqual(
            _validate_quick_replies(result, messages), conv._POST_ITINERARY_CHIPS
        )

    def test_slot_filling_filter_still_applies(self) -> None:
        """The pre-existing missing-slot path must be unchanged."""
        result = ExtractionResult(
            on_topic=True,
            slots=SlotValues(),
            missing_required=["destination", "budget"],
            ready_to_plan=False,
            quick_reply_options=["Tokyo", "Lisbon", "Around $3,000"],
        )
        self.assertEqual(_validate_quick_replies(result, []), ["Tokyo", "Lisbon"])

    def test_no_chips_when_about_to_plan(self) -> None:
        result = self._result(options=["Tokyo"])
        result.ready_to_plan = True
        self.assertEqual(_validate_quick_replies(result, []), [])


class FollowUpFallbackTests(unittest.TestCase):
    def test_generic_fallback_suppressed_once_a_trip_is_known(self) -> None:
        extraction = ExtractionResult(
            slots=SlotValues(destination="Barcelona", budget=3000),
            quick_reply_options=[],
        )
        self.assertEqual(build_follow_ups(extraction), [])

    def test_generic_fallback_used_on_a_cold_start(self) -> None:
        extraction = ExtractionResult(slots=SlotValues(), quick_reply_options=[])
        labels = [chip["label"] for chip in build_follow_ups(extraction)]
        self.assertIn("Help me choose a destination", labels)

    def test_confirmation_chips_survive_wire_shaping(self) -> None:
        extraction = ExtractionResult(
            slots=SlotValues(destination="Barcelona"),
            quick_reply_options=list(conv._CONFIRM_CHIPS),
        )
        self.assertEqual(
            [chip["value"] for chip in build_follow_ups(extraction)],
            conv._CONFIRM_CHIPS,
        )


class PromptTests(unittest.TestCase):
    """The prompt half of the fix. Can't assert on model behaviour offline, but
    the template is `.format()`-ed at call time with literal JSON braces in it,
    so a new rule containing an unescaped brace would raise KeyError on every
    single turn — worth pinning."""

    def test_extraction_prompt_still_formats(self) -> None:
        messages = turns(("user", "Barcelona in September"))
        built = conv._build_extraction_messages(messages)
        system = built[0]["content"]
        self.assertEqual(built[0]["role"], "system")
        self.assertEqual(built[1]["role"], "user")
        # The {today} placeholder must be resolved, not left literal.
        self.assertNotIn("{today}", system)
        self.assertIn("Analyze the FULL conversation", system)

    def test_new_rules_reach_the_model(self) -> None:
        system = conv._build_extraction_messages(turns(("user", "hi")))[0]["content"]
        for fragment in (
            # apply-now instruction is its own confirmation
            "explicit instruction to APPLY",
            # generous reading of a confirmation
            "does not have to be the first word",
            # never claim a pending change was stored
            "NEVER claim",
            # post-itinerary chips are next-messages, not slot answers
            "stop being slot answers",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, system)

    def test_reply_prompt_forbids_false_promises_on_a_pending_turn(self) -> None:
        messages = turns(
            ("user", "5 days in Barcelona"),
            ("assistant", f"Here you go! {MARKER}"),
            ("user", "add packing tips"),
        )
        extraction = ExtractionResult(
            on_topic=True,
            ready_to_plan=False,
            missing_required=[],
            slots=SlotValues(**planned_slots(special_requests="add packing tips")),
        )
        user_msg = conv.build_reply_messages(messages, extraction)[1]["content"]
        self.assertIn("nothing is stored between turns", user_msg.lower())

    def test_reply_prompt_on_a_confirmed_replan(self) -> None:
        messages = turns(
            ("user", "5 days in Barcelona"),
            ("assistant", f"Here you go! {MARKER}"),
            ("user", "yes go ahead"),
        )
        extraction = ExtractionResult(
            on_topic=True,
            ready_to_plan=True,
            missing_required=[],
            slots=SlotValues(**planned_slots(special_requests="add packing tips")),
        )
        user_msg = conv.build_reply_messages(messages, extraction)[1]["content"]
        self.assertIn("updating it now", user_msg)
        self.assertIn("add packing tips", user_msg)


if __name__ == "__main__":
    unittest.main()
