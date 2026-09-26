# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# ruff: noqa: D100, D101, D102, D103, D107

"""The tool-argument hook: canonical values, pattern checks, local answers, retry-guard keys."""

from __future__ import annotations

import json
import unittest
from dataclasses import replace

from prototypes.text_frontend_backend_agent.messages import ToolCall, canonical_json
from prototypes.voice_frontend_backend_agent.normalization.arguments import (
    REASON_ALREADY_FAILED,
    REASON_INVALID,
    ArgumentNormalizer,
    ArgumentRule,
    RetryGuardSettings,
    ToolArgumentSettings,
    render_message,
)
from prototypes.voice_frontend_backend_agent.normalization.transcript import TranscriptSettings

RULE = ArgumentRule(
    tool="get_user_details",
    argument="user_id",
    label="user ID",
    format_hint="firstname_lastname_1234",
    spoken_form=True,
    strip=" .-",
    collapse_separators="_",
    case="lower",
    pattern=r"^[a-z]+_[a-z]+_\d{4}$",
)
GUARD = RetryGuardSettings(enabled=True, permanent_failure_pattern=r"^Error: .*\bnot found\b")
SETTINGS = ToolArgumentSettings(enabled=True, rules=(RULE,), retry_guard=GUARD)


def normalizer(settings: ToolArgumentSettings = SETTINGS) -> ArgumentNormalizer:
    return ArgumentNormalizer(
        settings,
        transcript=TranscriptSettings(),
        invalid_template="bad {label} {value} ({format_hint})",
        already_failed_template="failed {tool} {label} {value}: {spelled}",
    )


def call(user_id: object, *, name: str = "get_user_details", call_id: str = "c1") -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments_json=canonical_json({"user_id": user_id}))


class CanonicalTests(unittest.TestCase):
    def test_case_separators_and_spoken_form(self) -> None:
        gate = normalizer()
        cases = {
            "Mia_Kim_4397": "mia_kim_4397",
            "MIA_KIM_4397": "mia_kim_4397",
            "mia__kim_4397_": "mia_kim_4397",
            "mia.kim_4397": "miakim_4397",
            "Mia underscore Kim underscore four three nine seven": "mia_kim_4397",
            "A_A_R_A_V_G_A_R_C_I_A_1177": "a_a_r_a_v_g_a_r_c_i_a_1177",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(gate.canonical(RULE, raw), expected)

    def test_pattern(self) -> None:
        self.assertTrue(ArgumentNormalizer.valid(RULE, "mia_kim_4397"))
        for value in ("ya", "699", "_7340", "a_a_r_a_v_g_a_r_c_i_a_1177", "miakim_4397"):
            self.assertFalse(ArgumentNormalizer.valid(RULE, value), value)
        self.assertTrue(ArgumentNormalizer.valid(replace(RULE, pattern=""), "anything"))


class ScreenTests(unittest.TestCase):
    def test_rewrite_is_sent_with_canonical_arguments(self) -> None:
        screening = normalizer().screen([call("Mia_Kim_4397")], frozenset())
        (sent,) = screening.sent
        self.assertEqual(json.loads(sent.arguments_json), {"user_id": "mia_kim_4397"})
        (rewrite,) = screening.rewrites
        self.assertEqual((rewrite.before, rewrite.after), ("Mia_Kim_4397", "mia_kim_4397"))
        self.assertEqual(screening.keys["c1"], ("get_user_details", canonical_json({"user_id": "mia_kim_4397"})))

    def test_invalid_is_answered_locally_and_asks_for_the_whole_value(self) -> None:
        screening = normalizer().screen([call("YA")], frozenset())
        self.assertEqual(screening.sent, ())
        (answer,) = screening.local
        self.assertEqual((answer.reason, answer.value), (REASON_INVALID, "ya"))
        self.assertEqual(answer.message, "bad user ID ya (firstname_lastname_1234)")

    def test_on_invalid_send_only_rewrites(self) -> None:
        settings = replace(SETTINGS, rules=(replace(RULE, on_invalid="send"),))
        screening = normalizer(settings).screen([call("YA")], frozenset())
        self.assertEqual(len(screening.sent), 1)
        self.assertEqual(screening.local, ())

    def test_already_failed_is_answered_locally_with_a_read_back(self) -> None:
        key = ("get_user_details", canonical_json({"user_id": "mia_kim_4397"}))
        screening = normalizer().screen([call("MIA_KIM_4397")], frozenset({key}))
        (answer,) = screening.local
        self.assertEqual(answer.reason, REASON_ALREADY_FAILED)
        self.assertIn("m, i, a, underscore, k, i, m, underscore, 4, 3, 9, 7", answer.message)

    def test_other_tools_and_malformed_arguments_pass_through(self) -> None:
        other = ToolCall(id="c2", name="get_reservation_details", arguments_json='{"reservation_id": "h9z1c"}')
        broken = ToolCall(id="c3", name="get_user_details", arguments_json="{not json")
        not_a_string = call(4397, call_id="c4")
        screening = normalizer().screen([other, broken, not_a_string], frozenset())
        self.assertEqual(screening.sent, (other, broken, not_a_string))
        self.assertEqual(screening.rewrites, ())
        self.assertNotIn("c2", screening.keys)  # scope: rules

    def test_scope_all_guards_every_tool(self) -> None:
        settings = replace(SETTINGS, retry_guard=replace(GUARD, scope="all"))
        other = ToolCall(id="c2", name="get_reservation_details", arguments_json='{"reservation_id": "h9z1c"}')
        key = ("get_reservation_details", canonical_json({"reservation_id": "h9z1c"}))
        screening = normalizer(settings).screen([other], frozenset({key}))
        self.assertEqual(screening.local[0].reason, REASON_ALREADY_FAILED)

    def test_only_permanent_failures_count(self) -> None:
        gate = normalizer()
        self.assertTrue(gate.is_permanent_failure("Error: User mia_kim_4397 not found"))
        self.assertFalse(gate.is_permanent_failure("Error: service unavailable, try again"))
        self.assertFalse(gate.is_permanent_failure('{"user_id": "mia_kim_4397"}'))
        disabled = normalizer(replace(SETTINGS, retry_guard=RetryGuardSettings()))
        self.assertFalse(disabled.is_permanent_failure("Error: User x not found"))


class MessageTests(unittest.TestCase):
    def test_literal_substitution(self) -> None:
        self.assertEqual(render_message(" {a} and {b} {c} ", a="1", b="{a}"), "1 and {a} {c}")
