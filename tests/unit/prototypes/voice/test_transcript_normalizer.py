# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# ruff: noqa: D100, D101, D102, D103, D107

"""The transcript hook: anchored spans only, real 800 ms-run transcripts as a corpus."""

from __future__ import annotations

import unittest

from prototypes.voice_frontend_backend_agent.normalization.transcript import (
    SpelledRunSettings,
    TranscriptNormalizer,
    TranscriptSettings,
)

LOWER = TranscriptNormalizer(TranscriptSettings(enabled=True, case="lower"))

#: ASR finals from the tau3 airline 800 ms run (logs/fba_voice_events.jsonl) and their written form.
CORPUS = (
    ("my user ID is Mia underscore Kim underscore four three nine seven.", "my user ID is mia_kim_4397."),
    ("M A underscore K im underscore nine nine five seven reason", "ma_kim_9957 reason"),
    ("Underscore nine nine five seven .", "_9957 ."),
    (
        "Yeah sorry user ID is MIA underscore KIM underscore four three nine seven and the reservation code is H"
        " nine ZU one C",
        "Yeah sorry user ID is mia_kim_4397 and the reservation code is H nine ZU one C",
    ),
    ("S O P H I A underscore S ilva underscore seven five five seven", "sophia_silva_7557"),
    ("Sure it's S O P H I A underscore Sil v a underscore seven five five seven .", "Sure it's sophia_silva_7557 ."),
    ("Yeah , it's O L I V I A underscore G O N Z A L E Z", "Yeah , it's olivia_gonzalez"),
    ("Yeah M O H A M E D underscore Sil Z A underscore nine two six five .", "Yeah mohamed_silza_9265 ."),
    (
        "Yeah , it's J am es underscore T A Y L O R underscore seven zero four three .",
        "Yeah , it's james_taylor_7043 .",
    ),
    ("A a rav underscore a h M E D underscore six six nine nine", "aarav_ahmed_6699"),
    ("It's the same O M A R underscore R O S S I underscore one two four one", "It's the same omar_rossi_1241"),
    ("Underscore seven five five seven . Did you get that ?", "_7557 . Did you get that ?"),
    ("Yeah A M E L I A underscore", "Yeah amelia_"),
    ("L E M N A underscore K im underscore nine nine five seven", "lemna_kim_9957"),
    ("Whole thing R A J underscore S.A.N.", "Whole thing raj_san."),
    ("Yeah A A R A V underscore A.H.", "Yeah aarav_ah."),  # dotted letters are spelled, not the filler "ah"
)


class TranscriptNormalizerTests(unittest.TestCase):
    def test_corpus(self) -> None:
        for raw, expected in CORPUS:
            with self.subTest(raw=raw):
                self.assertEqual(LOWER.normalize(raw).text, expected)

    def test_idempotent(self) -> None:
        for raw, _ in CORPUS:
            once = LOWER.normalize(raw).text
            self.assertEqual(LOWER.normalize(once).text, once)
            self.assertFalse(LOWER.normalize(once).changed)

    def test_no_anchor_no_change(self) -> None:
        for text in ("I need two passengers", "Book four three nine seven", "underscore", "Underscore."):
            result = LOWER.normalize(text)
            self.assertEqual(result.text, text)
            self.assertFalse(result.changed)

    def test_spans_record_spoken_and_written_forms(self) -> None:
        result = LOWER.normalize("It's Mia underscore Kim underscore double four nine seven, thanks.")
        self.assertEqual(result.text, "It's mia_kim_4497, thanks.")
        (span,) = result.spans
        self.assertEqual(
            (span.spoken, span.written), ("Mia underscore Kim underscore double four nine seven", "mia_kim_4497")
        )

    def test_stop_words_bound_the_left_part(self) -> None:
        self.assertEqual(LOWER.normalize("my name is Em ma underscore Kim").text, "my name is emma_kim")

    def test_too_many_words_between_anchors_make_separate_spans(self) -> None:
        normalizer = TranscriptNormalizer(TranscriptSettings(enabled=True, case="lower", max_part_tokens=2))
        text = "Mia underscore then we talked about many things underscore four"
        self.assertEqual(normalizer.normalize(text).text, "mia_then we talked about many things_4")

    def test_case_keep_and_number_words_off(self) -> None:
        keep = TranscriptNormalizer(TranscriptSettings(enabled=True))
        self.assertEqual(keep.normalize("Mia underscore Kim underscore four").text, "Mia_Kim_4")
        words = TranscriptNormalizer(TranscriptSettings(enabled=True, number_words=False))
        self.assertEqual(words.normalize("Mia underscore Kim underscore four").text, "Mia_Kim_four")

    def test_custom_separator_words(self) -> None:
        normalizer = TranscriptNormalizer(TranscriptSettings(enabled=True, separator_words={"dash": "-"}))
        self.assertEqual(normalizer.normalize("code A B dash four two").text, "code AB-42")
        self.assertEqual(normalizer.normalize("Mia underscore Kim").text, "Mia underscore Kim")


SPELLED = TranscriptNormalizer(TranscriptSettings(enabled=True, case="lower", spelled_runs=SpelledRunSettings(True)))


class SpelledRunTests(unittest.TestCase):
    def test_runs_are_joined_with_the_asr_case_and_stop_at_words(self) -> None:
        cases = {
            "I, F, O, Y, Y, Z and N, Q, N, U, five, R": "IFOYYZ and NQNU5R",
            "R O S S I": "ROSSI",
            "my name is R, uh, O S S I.": "my name is ROSSI.",
            "G V one N six four": "GV1N64",
            "from J F K to L A X": "from JFK to LAX",
        }
        for spoken, written in cases.items():
            with self.subTest(spoken=spoken):
                self.assertEqual(SPELLED.normalize(spoken).text, written)

    def test_unchanged_without_a_letter_or_below_min_tokens(self) -> None:
        for text in ("five zero zero", "one four four.", "I want a flight", "plan A or B", "A B"):
            with self.subTest(text=text):
                self.assertEqual(SPELLED.normalize(text).text, text)

    def test_anchored_spans_win_and_are_unchanged(self) -> None:
        for spoken, written in CORPUS:
            if not any(len(token.strip(".,")) == 1 for token in written.split()):
                with self.subTest(spoken=spoken):
                    self.assertEqual(SPELLED.normalize(spoken).text, written)
        self.assertEqual(SPELLED.normalize("Aarav underscore A H underscore one two three four").text, "aarav_ah_1234")

    def test_off_is_byte_identical(self) -> None:
        for spoken, _ in CORPUS:
            with self.subTest(spoken=spoken):
                self.assertEqual(
                    TranscriptNormalizer(TranscriptSettings(enabled=True, case="lower")).normalize(spoken),
                    LOWER.normalize(spoken),
                )
        self.assertEqual(LOWER.normalize("R O S S I").text, "R O S S I")
