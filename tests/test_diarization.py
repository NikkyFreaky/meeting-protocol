import os
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from backend.pipeline import UNKNOWN_SPEAKER, PipelineError, Word, _diarize, _pyannote_device, align_words


class ShortSpeakerClusterTests(TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, DIARIZATION_BACKEND="sherpa"))
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


class PyannoteTests(TestCase):
    def test_auto_uses_cpu_and_explicit_cuda_reports_missing_support(self):
        torch = Mock()
        torch.cuda.is_available.return_value = False
        with patch.dict("sys.modules", torch=torch):
            with patch.dict(os.environ, DIARIZATION_DEVICE="auto"):
                self.assertEqual(_pyannote_device(), "cpu")
            with patch.dict(os.environ, DIARIZATION_DEVICE="cuda"):
                with self.assertRaisesRegex(PipelineError, "CUDA"):
                    _pyannote_device()

    def test_uses_cuda_and_aligns_exclusive_turns_with_known_count(self):
        torch = Mock()
        torch.cuda.is_available.return_value = True
        diarizer = Mock()
        first = SimpleNamespace(start=0, end=4)
        second = SimpleNamespace(start=5, end=8)
        diarizer.return_value.exclusive_speaker_diarization = [(second, "B"), (first, "A")]
        samples = object()
        with (
            patch.dict(os.environ, DIARIZATION_BACKEND="pyannote", DIARIZATION_DEVICE="auto"),
            patch.dict("sys.modules", torch=torch),
            patch("backend.pipeline._load_pyannote", return_value=diarizer) as load,
        ):
            turns, speakers = _diarize(samples, 2, 7, Mock())
        self.assertEqual(load.call_args.args[1], "cuda")
        torch.from_numpy.assert_called_once_with(samples)
        torch.from_numpy.return_value.unsqueeze.assert_called_once_with(0)
        self.assertEqual(diarizer.call_args.args[0]["sample_rate"], 16000)
        self.assertEqual(diarizer.call_args.kwargs["num_speakers"], 2)
        self.assertEqual([(turn.speaker, turn.end) for turn in turns], [("speaker-1", 4), ("speaker-2", 7)])
        self.assertEqual(list(speakers), ["speaker-1", "speaker-2"])
