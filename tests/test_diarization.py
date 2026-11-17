from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from backend.pipeline import UNKNOWN_SPEAKER, Word, _diarize, align_words


class ShortSpeakerClusterTests(TestCase):
    def setUp(self):
        self.model = Mock()
        self.model.process.return_value.sort_by_start_time.return_value = [
            SimpleNamespace(start=0, end=4, speaker=0),
            SimpleNamespace(start=5, end=5.5, speaker=1),
            SimpleNamespace(start=6, end=6.7, speaker=1),
        ]

    def test_automatic_mode_keeps_words_without_counting_a_short_cluster_as_a_speaker(self):
        with patch("backend.pipeline._load_diarizer", return_value=self.model):
            turns, speakers = _diarize([], None, 7, Mock())
        self.assertEqual(list(speakers), ["speaker-1"])
        self.assertEqual([turn.speaker for turn in turns], ["speaker-1", UNKNOWN_SPEAKER, UNKNOWN_SPEAKER])
        segments = align_words([Word(5.1, 5.4, "Да.")], turns)
        self.assertEqual(segments[0]["speaker"], UNKNOWN_SPEAKER)
        self.assertEqual(segments[0]["text"], "Да.")

    def test_known_count_keeps_a_participant_who_spoke_briefly(self):
        with patch("backend.pipeline._load_diarizer", return_value=self.model) as load:
            turns, speakers = _diarize([], 2, 7, Mock())
        self.assertEqual(load.call_args.args[1], 2)
        self.assertEqual(list(speakers), ["speaker-1", "speaker-2"])
        self.assertEqual([turn.speaker for turn in turns], ["speaker-1", "speaker-2", "speaker-2"])
