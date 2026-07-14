import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app import db
from app.corrections import (
    _is_protected_candidate,
    _normalize_protected_terms,
    extract_candidates,
    learn_from_text_edit,
)


class ProtectedSpeakerTermsTest(unittest.TestCase):
    @patch("app.corrections.db.save_correction_event")
    @patch("app.corrections.db.upsert_correction_rule")
    def test_field_edit_records_path_on_rule_example(self, upsert, save_event) -> None:
        upsert.return_value = {"wrong_text": "希淋", "correct_text": "西林"}

        learned = learn_from_text_edit(
            "meeting-1",
            "客户使用阿莫希淋",
            "客户使用阿莫西林",
            edit_path="sections.1.content",
        )

        self.assertTrue(learned)
        self.assertEqual(upsert.call_args.kwargs["example"]["edit_path"], "sections.1.content")
        save_event.assert_called_once()

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

    def test_correction_rule_can_be_edited_enabled_and_deleted(self) -> None:
        with TemporaryDirectory() as tmp:
            with patch.object(db.config, "DB_PATH", Path(tmp) / "test.db"):
                db.init_db()
                rule = db.upsert_correction_rule(
                    "旧术语", "新术语", confidence=0.4, example={"meeting_id": "m1"}
                )
                updated = db.update_correction_rule(
                    rule["id"], "错误术语", "正确术语", True
                )

                self.assertEqual(updated["wrong_text"], "错误术语")
                self.assertEqual(updated["correct_text"], "正确术语")
                self.assertEqual(updated["enabled"], 1)
                self.assertTrue(db.delete_correction_rule(rule["id"]))
                self.assertEqual(db.list_correction_rules(), [])

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
