# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# ruff: noqa: D100, D101, D102, D103, D107

"""Voice-side profiles: every one loads; 800 ms VAD, normalization and delay invariants; strict keys."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _fdh_voice_fakes import PROFILES

from prototypes.voice_delegation_hermes_agent.config import DelegationConfigError, load_delegation_config

SHIPPED = sorted(PROFILES.glob("*.yaml"))


class ShippedProfileTests(unittest.TestCase):
    def test_every_profile_loads_with_the_invariants(self) -> None:
        self.assertEqual(
            [p.name for p in SHIPPED],
            [
                "browser_demo.yaml",
                "stub.yaml",
                "stub_gate.yaml",
                "tau3_airline_canary.yaml",
                "tau3_arm_g1_filler_dedupe.yaml",
                "tau3_arm_g2_short_answers.yaml",
                "tau3_arm_m1_status.yaml",
                "tau3_arm_m2_replay.yaml",
                "tau3_arm_m3_spelling.yaml",
                "tau3_eval.yaml",
                "tau3_eval_baseline.yaml",
                "tau3_eval_silent_ack.yaml",
            ],
        )
        for path in SHIPPED:
            with self.subTest(profile=path.name):
                config = load_delegation_config(path)
                td = config.voice.turn_detection
                self.assertEqual((td.silence_duration_ms, td.honor_client_values), (800, False))
                self.assertTrue(config.voice.normalization.transcript.enabled)
                self.assertEqual(config.voice.server.ws_ping_interval_s, 0)
                expected_delay = 5.0 if path.name == "browser_demo.yaml" else 0.0
                self.assertEqual(config.backend.delay_seconds, expected_delay)

    def test_tau3_and_browser_specifics(self) -> None:
        tau3 = load_delegation_config(PROFILES / "tau3_eval.yaml")
        self.assertEqual(
            (tau3.voice.server.port, tau3.tools.executor, tau3.voice.tools.source), (8775, "wire", "client")
        )
        self.assertTrue(tau3.voice.normalization.tool_arguments.enabled)
        self.assertFalse(tau3.voice.protocol.greeting_enabled)
        browser = load_delegation_config(PROFILES / "browser_demo.yaml")
        self.assertEqual((browser.voice.server.port, browser.tools.executor), (8776, "local"))
        self.assertFalse(browser.voice.normalization.tool_arguments.enabled)
        self.assertTrue(browser.voice.protocol.greeting_enabled)
        self.assertEqual([spec.name for spec in browser.voice.tools.config_tool_specs], ["get_order", "cancel_order"])

    def test_baseline_is_tau3_eval_and_each_arm_turns_on_only_its_own_switch(self) -> None:
        baseline = load_delegation_config(PROFILES / "tau3_eval_baseline.yaml")
        self.assertEqual(baseline.config_hash, load_delegation_config(PROFILES / "tau3_eval.yaml").config_hash)
        self.assertFalse(any(v for k, v in baseline.features.items() if k != "prompt_features"))
        self.assertFalse(any(baseline.features["prompt_features"].values()))
        arms = {
            "tau3_arm_m1_status.yaml": {"proactive_status"},
            "tau3_arm_m2_replay.yaml": {"replay_unheard_answer", "replay_intent"},
            "tau3_arm_m3_spelling.yaml": {"spelling_hold", "spelled_runs"},
            "tau3_arm_g1_filler_dedupe.yaml": {"filler_dedupe"},
            "tau3_arm_g2_short_answers.yaml": {"clean_answers"},
            "tau3_airline_canary.yaml": {"proactive_status", "spelling_hold", "spelled_runs"},
        }
        for name, expected in arms.items():
            with self.subTest(profile=name):
                features = load_delegation_config(PROFILES / name).features
                on = {k for k, v in features.items() if v is True} | {
                    k for k, v in features["prompt_features"].items() if v
                }
                self.assertEqual(on, expected)
        m3 = load_delegation_config(PROFILES / "tau3_arm_m3_spelling.yaml")
        self.assertEqual(m3.delegation.spelling_hold.complete_patterns, ("^[A-Za-z0-9]{6}$",))
        self.assertEqual(m3.voice.normalization.tool_arguments.invalid_message_key, "tool_argument_invalid_readback")
        self.assertEqual(m3.voice.normalization.transcript.spelled_runs.case, "keep")

    def test_delay_is_env_configurable_in_the_browser_profile(self) -> None:
        with mock.patch.dict(os.environ, {"FDH_BACKEND_DELAY_S": "2.5"}):
            self.assertEqual(load_delegation_config(PROFILES / "browser_demo.yaml").backend.delay_seconds, 2.5)

    def test_secrets_are_redacted_and_hash_is_stable(self) -> None:
        with mock.patch.dict(os.environ, {"NVIDIA_API_KEY": "sk-secret"}):
            config = load_delegation_config(PROFILES / "tau3_eval.yaml")
            again = load_delegation_config(PROFILES / "tau3_eval.yaml")
        self.assertEqual(config.effective["frontend"]["llm"]["api_key"], "***")
        self.assertNotIn("sk-secret", str(config.effective))
        self.assertEqual(config.config_hash, again.config_hash)


class ValidationTests(unittest.TestCase):
    def load(self, text: str):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.yaml"
            path.write_text(f"extends: {PROFILES / 'tau3_eval.yaml'}\n{text}")
            return load_delegation_config(path)

    def test_unknown_key_enum_and_cross_checks(self) -> None:
        with self.assertRaisesRegex(DelegationConfigError, "unknown key 'backend.nope'"):
            self.load("backend: {nope: 1}\n")
        with self.assertRaisesRegex(DelegationConfigError, "backend.steer_mode"):
            self.load("backend: {steer_mode: sometimes}\n")
        with self.assertRaisesRegex(DelegationConfigError, "executor: local requires"):
            self.load("tools: {executor: local}\n")
        with self.assertRaisesRegex(DelegationConfigError, "output.on_stale"):
            self.load("output: {on_stale: {status: maybe}}\n")


if __name__ == "__main__":
    unittest.main()
