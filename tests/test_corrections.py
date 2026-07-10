import unittest

from app.corrections import (
    _is_protected_candidate,
    _normalize_protected_terms,
    extract_candidates,
)


class ProtectedSpeakerTermsTest(unittest.TestCase):
    def test_single_character_name_blocks_longer_candidate(self) -> None:
        # "王总" -> "李主任" is a valid candidate, even though the speaker
        # display name itself is only one character.
        candidates = extract_candidates(
            "\u738b\u603b\u8d1f\u8d23", "\u674e\u4e3b\u4efb\u8d1f\u8d23"
        )
        protected = _normalize_protected_terms(["\u738b"])

        self.assertTrue(candidates)
        self.assertTrue(
            all(
                _is_protected_candidate(c["wrong_text"], c["correct_text"], protected)
                for c in candidates
            )
        )

    def test_case_insensitive_name_blocks_fragment_candidate(self) -> None:
        candidates = extract_candidates("JHONA reports", "JOHNA reports")
        protected = _normalize_protected_terms(["John"])

        self.assertIn(("HO", "OH"), {(c["wrong_text"], c["correct_text"]) for c in candidates})
        self.assertTrue(
            all(
                _is_protected_candidate(c["wrong_text"], c["correct_text"], protected)
                for c in candidates
            )
        )


if __name__ == "__main__":
    unittest.main()
