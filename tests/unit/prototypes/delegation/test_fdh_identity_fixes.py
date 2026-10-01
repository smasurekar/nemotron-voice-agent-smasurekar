# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# ruff: noqa: D100, D101, D102, D103, D107

"""tau3-identity-fixes-plan.md: I1 (telecom domain note) and I3/I4 (recovery notes on failed lookups)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from _fdh_backend_fakes import Harness as ControllerHarness
from _fdh_voice_fakes import PACKAGE, PROFILES, DelegationHarness, profile_config, tau2_session_update

from prototypes.voice_delegation_hermes_agent.prompt_features import sha256_text
from prototypes.voice_delegation_hermes_agent.sidecar import gateway_config as gc
from prototypes.voice_delegation_hermes_agent.sidecar.templates import BackendTemplates
from prototypes.voice_delegation_hermes_agent.tools.result_hints import ResultHints
from prototypes.voice_frontend_backend_agent.config import load_voice_config
from prototypes.voice_frontend_backend_agent.errors import VoiceConfigError
from prototypes.voice_frontend_backend_agent.normalization.arguments import ResultHintSettings

CONFIG = PACKAGE / "config"
BACKEND_PROMPTS = CONFIG / "prompts.backend.yaml"
ALL_ON = {"spelling_v2": True, "spoken_output": True, "write_consent": True, "domain_notes": True}
ALL_ON_BUT_NOTES = {**ALL_ON, "domain_notes": False}
NOT_FOUND = "Error: User not found"
FOUND = '"sara_doe_496"'


def _tools(domain: str) -> list[str]:
    return [t["name"] for t in tau2_session_update(domain)["session"]["tools"]]


def _instructions(domain: str) -> str:
    return tau2_session_update(domain)["session"]["instructions"]


# -- I1: domain detection and the telecom note -----------------------------------------------------


class DomainNoteTests(unittest.TestCase):
    def test_shipped_detection_maps_each_tau2_domain(self) -> None:
        domains = gc.load_gateway_config(CONFIG / "gateway.yaml").domains
        detected = {}
        for domain in ("airline", "retail", "telecom", "mock"):
            names = set(_tools(domain))
            detected[domain] = next((name for name, tools in domains if names.intersection(tools)), "")
        self.assertEqual(detected, {"airline": "airline", "retail": "retail", "telecom": "telecom", "mock": ""})

    def test_note_renders_only_for_telecom_and_only_with_its_feature(self) -> None:
        on, off = BackendTemplates(BACKEND_PROMPTS, prompt_features=ALL_ON), BackendTemplates(BACKEND_PROMPTS)
        self.assertIn("555-123-4567", on.domain_note("telecom"))
        self.assertIn("exactly ten digits", on.domain_note("telecom"))
        self.assertIn("Never add, remove or change digits", on.domain_note("telecom"))
        self.assertNotIn("555-123-2002", on.domain_note("telecom"))  # never the benchmark's number
        for domain in ("airline", "retail", "mock", ""):
            self.assertEqual(on.domain_note(domain), "")
        self.assertEqual(off.domain_note("telecom"), "")
        self.assertEqual(on.domains_with_notes, ("telecom",))

    def test_note_changes_only_the_telecom_prompt_and_only_by_its_block(self) -> None:
        for features in (ALL_ON, {"domain_notes": True}):
            on = BackendTemplates(BACKEND_PROMPTS, prompt_features=features)
            without = BackendTemplates(BACKEND_PROMPTS, prompt_features={**features, "domain_notes": False})
            for domain in ("airline", "retail", "mock"):
                with self.subTest(features=features, domain=domain):
                    self.assertEqual(
                        on.render("backend_system", instructions=_instructions(domain), domain_note=""),
                        without.render("backend_system", instructions=_instructions(domain)),
                    )
            telecom = _instructions("telecom")
            note = on.domain_note("telecom")
            with_note = on.render("backend_system", instructions=telecom, domain_note=note)
            plain = without.render("backend_system", instructions=telecom)
            block = f"\n\n<domain_notes>\n{note}\n</domain_notes>"
            self.assertIn(block, with_note)
            self.assertEqual(with_note.replace(block, "", 1).rstrip(), plain)
            self.assertLess(with_note.index("</policy>"), with_note.index("<domain_notes>"))

    def test_gateway_domains_are_validated(self) -> None:
        for bad in ({"telecom": ["get_customer_by_phone"]}, {"telecom": {"tools_any": []}},
                    {"telecom": {"tools_any": ["x"], "tools_all": ["y"]}}, ["telecom"]):  # fmt: skip
            with self.subTest(bad=bad), self.assertRaises(gc.GatewayConfigError):
                gc.load_gateway_config(None, overrides={"domains": bad})
        self.assertEqual(gc.load_gateway_config(None).domains, ())


class ControllerDomainTests(unittest.IsolatedAsyncioTestCase):
    async def _configure(self, domain: str, features: dict[str, bool]) -> ControllerHarness:
        domains = gc.load_gateway_config(CONFIG / "gateway.yaml").domains
        h = ControllerHarness(domains=domains)
        h.controller._templates = BackendTemplates(BACKEND_PROMPTS, prompt_features=features)  # noqa: SLF001
        await h.controller.open({"steer_mode": "auto"})
        tools = tau2_session_update(domain)["session"]["tools"]
        await h.controller.configure(tools, _instructions(domain))
        return h

    async def test_telecom_worker_gets_the_note_and_the_fingerprint_hashes_what_it_got(self) -> None:
        h = await self._configure("telecom", ALL_ON)
        system = h.worker.of_type("configure")[0]["instructions"]
        self.assertIn("<domain_notes>", system)
        configured = h.of_type("session.configured")[0]
        self.assertEqual(configured["backend_domain"], "telecom")
        self.assertEqual(configured["backend_system_sha256"], sha256_text(system))

    async def test_other_domains_and_the_control_get_no_note(self) -> None:
        for domain, features, expected in (
            ("retail", ALL_ON, "retail"),
            ("airline", ALL_ON, "airline"),
            ("mock", ALL_ON, ""),
            ("telecom", ALL_ON_BUT_NOTES, "telecom"),
        ):
            with self.subTest(domain=domain, features=features):
                h = await self._configure(domain, features)
                system = h.worker.of_type("configure")[0]["instructions"]
                self.assertNotIn("<domain_notes>", system)
                plain = BackendTemplates(BACKEND_PROMPTS, prompt_features=features).render(
                    "backend_system", instructions=_instructions(domain)
                )
                self.assertEqual(system, plain)
                configured = h.of_type("session.configured")[0]
                self.assertEqual(configured["backend_domain"], expected)
                self.assertEqual(configured["backend_system_sha256"], sha256_text(system))


# -- I3 / I4: the per-session miss counter ---------------------------------------------------------


def _hints(**overrides: object) -> ResultHints:
    settings = ResultHintSettings(
        **{
            "enabled": True,
            "tools": ("find_user_id_by_name_zip", "find_user_id_by_email", "get_user_details"),
            "failure_pattern": r"^Error: .*\bnot found\b",
            "message_key": "i3",
            "escalate_message_key": "i4",
            **overrides,
        }
    )
    return ResultHints(
        settings, message="[I3 note]", escalate_message="[I4 note]" if settings.escalate_message_key else ""
    )


class ResultHintsTests(unittest.TestCase):
    def test_first_miss_gets_i3_later_misses_i4_and_the_output_is_kept(self) -> None:
        hints = _hints()
        first = hints.on_output("find_user_id_by_name_zip", NOT_FOUND)
        assert first is not None
        self.assertEqual((first.output, first.message_key, first.miss), (f"{NOT_FOUND}\n\n[I3 note]", "i3", 1))
        second = hints.on_output("find_user_id_by_email", NOT_FOUND)
        assert second is not None
        self.assertEqual((second.message_key, second.miss), ("i4", 2))  # one counter for every watched lookup

    def test_success_resets_and_is_never_annotated(self) -> None:
        hints = _hints()
        hints.on_output("find_user_id_by_name_zip", NOT_FOUND)
        self.assertIsNone(hints.on_output("find_user_id_by_name_zip", FOUND))
        again = hints.on_output("find_user_id_by_name_zip", NOT_FOUND)
        assert again is not None
        self.assertEqual(again.message_key, "i3")

    def test_local_invalid_counts_toward_the_next_miss(self) -> None:
        hints = _hints()
        hints.on_local_invalid("get_user_details")
        hint = hints.on_output("get_user_details", "Error: User aarav_ahmed_6699 not found")
        assert hint is not None
        self.assertEqual((hint.message_key, hint.miss), ("i4", 2))
        uncounted = _hints(count_local_invalid=False)
        uncounted.on_local_invalid("get_user_details")
        hint = uncounted.on_output("get_user_details", "Error: User aarav_ahmed_6699 not found")
        assert hint is not None
        self.assertEqual(hint.message_key, "i3")

    def test_bounded_by_max_hints(self) -> None:
        hints = _hints(max_hints=3)
        got = [hints.on_output("find_user_id_by_name_zip", NOT_FOUND) for _ in range(5)]
        self.assertEqual([h is not None for h in got], [True, True, True, False, False])

    def test_unwatched_tools_and_other_errors_are_untouched(self) -> None:
        hints = _hints()
        self.assertIsNone(
            hints.on_output("get_customer_by_phone", "Error: Customer with phone number 5551232002 not found")
        )
        self.assertIsNone(hints.on_output("get_order_details", "Error: Order not found"))
        hints.on_output("find_user_id_by_name_zip", NOT_FOUND)
        self.assertIsNone(hints.on_output("find_user_id_by_name_zip", "Error: upstream timeout"))  # transient
        later = hints.on_output("find_user_id_by_name_zip", NOT_FOUND)
        assert later is not None
        self.assertEqual(later.message_key, "i4")  # a transient error does not reset the count

    def test_without_escalation_every_miss_gets_the_first_note(self) -> None:
        hints = _hints(escalate_message_key="")
        keys = [hints.on_output("find_user_id_by_name_zip", NOT_FOUND) for _ in range(2)]
        self.assertEqual([h.message_key for h in keys if h], ["i3", "i3"])
        self.assertTrue(all(h and h.output.endswith("[I3 note]") for h in keys))


# -- I3 / I4 through the voice server ---------------------------------------------------------------


class RelayHintTests(unittest.IsolatedAsyncioTestCase):
    async def _session(self, profile: str, domain: str) -> DelegationHarness:
        harness = DelegationHarness(profile_config(profile), transcripts=["I need help with my order"])
        await harness.start(tau2_session_update(domain))
        await harness.speak()
        await harness.wait_sent("delegate")
        harness.link.deliver("state", state="WORKING", epoch=1, run_id="run_1")
        return harness

    async def _round_trip(self, harness: DelegationHarness, index: int, name: str, args: str, output: str) -> str:
        call_id = f"c{index}"
        results = len(harness.link.of_type("tool.result"))
        harness.link.deliver("tool.call", call_id=call_id, epoch=1, run_id="run_1", name=name, arguments=args)
        await harness.wait_for("response.function_call_arguments.done", count=index)
        await harness.send(
            {
                "type": "conversation.item.create",
                "item": {"type": "function_call_output", "call_id": call_id, "output": output},
            }
        )
        await harness.send({"type": "response.create"})
        return (await harness.wait_sent("tool.result", count=results + 1))["output"]

    async def test_default_profile_annotates_retail_misses_and_leaves_successes(self) -> None:
        harness = await self._session("tau3_eval.yaml", "retail")
        args = '{"first_name": "Isabela", "last_name": "Johansson", "zip": "32286"}'
        first = await self._round_trip(harness, 1, "find_user_id_by_name_zip", args, NOT_FOUND)
        second = await self._round_trip(harness, 2, "find_user_id_by_name_zip", args, NOT_FOUND)
        found = await self._round_trip(harness, 3, "find_user_id_by_name_zip", args, FOUND)
        self.assertTrue(first.startswith(NOT_FOUND + "\n\n[Not found. At least one value"))
        self.assertTrue(second.startswith(NOT_FOUND + "\n\n[Not found again."))
        self.assertIn('"S as in Sam', second)
        self.assertEqual(found, FOUND)
        logged = [(e["message_key"], e["n"]) for e in harness.logged("result_hint")]
        self.assertEqual(logged, [("identity_not_found_hint", 1), ("identity_not_found_hint_words", 2)])
        start = harness.logged("fdh_session_start")[0]
        self.assertTrue(start["features"]["result_hints"])
        self.assertEqual(start["invalid_message_keys"]["result_hint_escalate"], "identity_not_found_hint_words")
        await harness.close()

    async def test_control_and_telecom_outputs_are_byte_identical(self) -> None:
        for profile, domain, name, args, output in (
            ("tau3_eval_baseline.yaml", "retail", "find_user_id_by_email", '{"email": "a@b.com"}', NOT_FOUND),
            ("tau3_identity_control.yaml", "retail", "find_user_id_by_email", '{"email": "a@b.com"}', NOT_FOUND),
            ("tau3_eval.yaml", "telecom", "get_customer_by_phone", '{"phone_number": "5551232002"}',
             "Error: Customer with phone number 5551232002 not found"),
        ):  # fmt: skip
            with self.subTest(profile=profile, domain=domain):
                harness = await self._session(profile, domain)
                self.assertEqual(await self._round_trip(harness, 1, name, args, output), output)
                self.assertEqual(harness.logged("result_hint"), [])
                await harness.close()

    async def test_airline_local_invalid_then_external_miss_gets_i4(self) -> None:
        harness = await self._session("tau3_eval.yaml", "airline")
        harness.link.deliver(
            "tool.call",
            call_id="c0",
            epoch=1,
            run_id="run_1",
            name="get_user_details",
            arguments='{"user_id": "aarav_ah"}',
        )
        local = await harness.wait_sent("tool.result")
        self.assertTrue(local["local"])
        self.assertNotIn("[Not found", local["output"])  # a local answer is never annotated
        output = await self._round_trip(
            harness, 1, "get_user_details", '{"user_id": "aarav_ahmed_6699"}', "Error: User aarav_ahmed_6699 not found"
        )
        self.assertIn("[Not found again.", output)
        await harness.close()

    async def test_i3_arm_repeats_the_first_note(self) -> None:
        harness = await self._session("tau3_arm_i3_recovery_hint.yaml", "retail")
        outputs = [
            await self._round_trip(harness, i, "find_user_id_by_email", '{"email": "a@b.com"}', NOT_FOUND)
            for i in (1, 2)
        ]
        self.assertTrue(all("[Not found. At least one value" in o for o in outputs))
        await harness.close()


class ResultHintConfigTests(unittest.TestCase):
    def _load(self, text: str):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "voice.yaml"
            path.write_text(f"extends: {CONFIG / 'voice' / 'tau3.yaml'}\n{text}")
            return load_voice_config(path)

    def test_defaults_are_off(self) -> None:
        hints = self._load("").normalization.tool_arguments.result_hints
        self.assertFalse(hints.enabled)
        self.assertEqual((hints.max_hints, hints.count_local_invalid), (3, True))

    def test_invalid_settings_fail_at_load(self) -> None:
        base = "normalization:\n  tool_arguments:\n    result_hints:\n      enabled: true\n"
        good = "      tools: [find_user_id_by_email]\n      failure_pattern: 'not found'\n"
        for extra in (
            "      failure_pattern: 'not found'\n",  # no tools
            "      tools: [find_user_id_by_email]\n",  # no pattern
            good + "      failure_pattern: '('\n",  # bad regex
            good + "      message_key: no_such_key\n",  # not in the catalog
            good + "      escalate_message_key: no_such_key\n",
            good + "      max_hints: 0\n",
        ):
            with self.subTest(extra=extra), self.assertRaises(VoiceConfigError):
                self._load(base + extra)
        off = "normalization:\n  tool_arguments:\n    enabled: false\n"
        with self.assertRaises(VoiceConfigError):
            self._load(off + "    result_hints:\n      enabled: true\n" + good)
        self.assertTrue(self._load(base + good).normalization.tool_arguments.result_hints.enabled)

    def test_shipped_voice_profiles(self) -> None:
        voice = CONFIG / "voice"
        on = {
            p.name
            for p in voice.glob("*.yaml")
            if load_voice_config(p).normalization.tool_arguments.result_hints.enabled
        }
        self.assertEqual(on, {"tau3_fixes.yaml", "tau3_recovery_hint.yaml"})
        self.assertTrue((PROFILES / "tau3_arm_i3_recovery_hint.yaml").exists())


if __name__ == "__main__":
    unittest.main()
