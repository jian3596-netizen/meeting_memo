import unittest
from pathlib import Path
from unittest.mock import patch

from app import main, pipeline
from app.models import RegenerateRequest, Segment
from fastapi import HTTPException


class ManualSummaryFlowTests(unittest.TestCase):
    @patch("app.pipeline.get_llm")
    @patch("app.pipeline._auto_match_speakers")
    @patch("app.pipeline.corrections.apply_enabled_rules_to_segments")
    @patch("app.pipeline.clean_segments")
    @patch("app.pipeline.transcribe_job")
    @patch("app.pipeline.audio.prepare")
    @patch("app.pipeline.db")
    def test_upload_pipeline_stops_after_saved_transcript(
        self, db_mock, prepare_mock, transcribe_mock, _clean_mock,
        _corrections_mock, _voiceprint_mock, get_llm_mock,
    ):
        db_mock.get_meeting.return_value = {
            "id": "m1", "audio_path": "", "spk_num": 0,
        }
        db_mock.get_hotwords.return_value = []
        db_mock.get_voiceprints.return_value = []
        prepare_mock.return_value = (Path("processed.wav"), 12.0)
        transcribe_mock.return_value = ([
            Segment(
                idx=0, speaker="SPEAKER_00", text="测试",
                raw_text="测试", end_seconds=1.0,
            )
        ], {})

        pipeline.process_meeting("m1")

        db_mock.save_segments.assert_called_once()
        db_mock.set_status.assert_any_call("m1", "transcribed", 75)
        get_llm_mock.assert_not_called()

    @patch("app.main.db.get_speaker_map", return_value={"SPEAKER_00": "张三"})
    @patch("app.main.db.get_segment_rows", return_value=[
        {"speaker": "SPEAKER_00"}, {"speaker": "SPEAKER_01"},
    ])
    def test_missing_speaker_names_are_reported(self, _rows, _mapping):
        self.assertEqual(main._missing_speaker_names("m1"), ["SPEAKER_01"])

    @patch("app.main._spawn")
    @patch("app.main.db.get_speaker_map", return_value={})
    @patch("app.main.db.get_segment_rows", return_value=[{"speaker": "SPEAKER_00"}])
    @patch("app.main.db.get_meeting", return_value={"id": "m1", "status": "transcribed"})
    def test_summary_is_rejected_until_all_speakers_are_named(
        self, _meeting, _rows, _mapping, spawn_mock,
    ):
        with self.assertRaises(HTTPException) as ctx:
            main.regenerate("m1", RegenerateRequest())
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("SPEAKER_00", ctx.exception.detail)
        spawn_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
