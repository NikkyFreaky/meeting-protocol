"""Transcribe local audio and assign each word to a speaker turn."""

import os
from bisect import bisect_left
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import lru_cache
from itertools import accumulate
from pathlib import Path
from time import monotonic
from typing import TypedDict

from .protocol import Protocol, TranscriptSegment, build_protocol
from .setup_models import download_diarization_models, model_cache_dir, whisper_model_name


SAMPLE_RATE = 16000
UNKNOWN_SPEAKER = "unknown"
ProgressLog = Callable[[str, str], None]


class PipelineError(RuntimeError):
    """An actionable processing or configuration error safe for the interface."""


class MeetingResult(TypedDict):
    duration: float
    speakers: dict[str, str]
    segments: list[TranscriptSegment]
    protocol: Protocol


@dataclass(frozen=True)
class SpeakerTurn:
    start: float
    end: float
    speaker: str


@dataclass(frozen=True)
class Word:
    start: float
    end: float
    text: str


def _speaker_for_word(word: Word, turns: list[SpeakerTurn], ends: list[float]) -> str:
    # The prefix maximum of turn ends lets us skip older turns even when two
    # speakers overlap. A small margin accommodates slight timestamp drift.
    margin = 0.25
    best_speaker = UNKNOWN_SPEAKER
    best_score = (-1.0, -margin, float("-inf"))
    middle = (word.start + word.end) / 2
    for index in range(bisect_left(ends, word.start - margin), len(turns)):
        turn = turns[index]
        if turn.start > word.end + margin:
            break
        overlap = min(word.end, turn.end) - max(word.start, turn.start)
        score = (max(0.0, overlap), min(0.0, overlap), -abs(middle - (turn.start + turn.end) / 2))
        if score > best_score:
            best_speaker = turn.speaker
            best_score = score
    return best_speaker


def align_words(words: Iterable[Word], turns: list[SpeakerTurn]) -> list[TranscriptSegment]:
    turns = sorted(turns, key=lambda turn: (turn.start, turn.end, turn.speaker))
    ends = list(accumulate((turn.end for turn in turns), max))
    result: list[TranscriptSegment] = []

    for word in words:
        token = word.text.strip()
        if not token:
            continue
        speaker = _speaker_for_word(word, turns, ends)
        current = result[-1] if result else None
        if (
            current is None
            or current["speaker"] != speaker
            or word.start > current["end"] + 0.9
            or word.start >= current["start"] + 18
            or (current["text"].endswith((".", "!", "?")) and token[0].isupper())
        ):
            result.append({"start": word.start, "end": word.end, "speaker": speaker, "text": token})
            continue

        separator = "" if token[0] in ",.!?:;%)]»" or current["text"].endswith(("(", "«")) else " "
        current["text"] += separator + token
        current["end"] = max(current["end"], word.end)

    for segment in result:
        segment["start"] = round(float(segment["start"]), 3)
        segment["end"] = round(float(segment["end"]), 3)
    return result


@lru_cache(maxsize=1)
def _load_diarizer(cache_dir: Path, speaker_count: int):
    try:
        import sherpa_onnx
    except (ImportError, OSError) as error:
        raise PipelineError("Не удалось загрузить Sherpa ONNX. Установите зависимости из requirements-ml.txt.") from error

    try:
        segmentation_path, embedding_path = download_diarization_models(cache_dir)
    except Exception as error:
        raise PipelineError("Не удалось скачать модели диаризации. Проверьте доступ к Hugging Face или запустите python -m backend.setup_models.") from error

    config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=segmentation_path),
        ),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=embedding_path),
        clustering=sherpa_onnx.FastClusteringConfig(num_clusters=speaker_count, threshold=0.55),
        min_duration_on=0.3,
        min_duration_off=0.5,
    )
    if not config.validate():
        raise PipelineError("Sherpa ONNX не смогла проверить файлы моделей диаризации.")
    return sherpa_onnx.OfflineSpeakerDiarization(config)


@lru_cache(maxsize=1)
def _load_whisper(model_name: str, cache_dir: Path, cpu_threads: int):
    from faster_whisper import WhisperModel

    try:
        return WhisperModel(
            model_name, device="cpu", compute_type="int8", cpu_threads=cpu_threads,
            download_root=str(cache_dir), use_auth_token=False,
        )
    except Exception as error:
        raise PipelineError("Не удалось загрузить Whisper. Проверьте WHISPER_MODEL и доступность файлов модели.") from error


def _decode(path: Path):
    try:
        from faster_whisper.audio import decode_audio
    except (ImportError, OSError) as error:
        raise PipelineError("Не удалось загрузить Faster Whisper. Установите зависимости из requirements-ml.txt.") from error

    try:
        samples = decode_audio(str(path), sampling_rate=SAMPLE_RATE)
    except Exception as error:
        raise PipelineError("Не удалось прочитать звуковую дорожку. Проверьте формат и целостность записи.") from error

    if not samples.size:
        raise PipelineError("В файле нет звуковых данных.")
    return samples


def _diarize(samples, expected_speakers: int | None, duration: float, log: ProgressLog) -> tuple[list[SpeakerTurn], dict[str, str]]:
    last_report = float("-inf")

    def report_chunks(processed, total):
        nonlocal last_report
        now = monotonic()
        if total > 0 and (processed == total or now - last_report >= 10):
            log("info", f"Диаризация: обработано фрагментов {processed} из {total}.")
            last_report = now
        return 0

    try:
        log("info", "Подготавливаем модель Sherpa ONNX.")
        diarizer = _load_diarizer(model_cache_dir(), expected_speakers or -1)
        mode = f"задано число участников: {expected_speakers}" if expected_speakers else "число участников определяется автоматически"
        log("info", f"Анализируем голоса и паузы, {mode}.")
        intervals = diarizer.process(samples, callback=report_chunks).sort_by_start_time()
    except PipelineError:
        raise
    except Exception as error:
        raise PipelineError("Модель не смогла разделить запись на спикеров.") from error

    ids: dict[int, str] = {}
    speakers: dict[str, str] = {}
    turns: list[SpeakerTurn] = []
    for interval in intervals:
        start = max(0.0, float(interval.start))
        end = min(duration, float(interval.end))
        label = interval.speaker
        if end <= start:
            continue
        if label not in ids:
            number = len(ids) + 1
            ids[label] = f"speaker-{number}"
            speakers[ids[label]] = f"Спикер {number}"
        turns.append(SpeakerTurn(start, end, ids[label]))
    return turns, speakers


def _whisper_words(samples, duration: float, cpu_threads: int, log: ProgressLog) -> Iterable[Word]:
    language = os.getenv("WHISPER_LANGUAGE", "ru").strip().lower()
    log("info", "Подготавливаем модель Whisper.")
    whisper = _load_whisper(whisper_model_name(), model_cache_dir(), cpu_threads)
    try:
        log("info", "Ищем речь и включаем временные метки слов.")
        segments, info = whisper.transcribe(
            samples,
            language=None if language in ("", "auto") else language,
            task="transcribe",
            word_timestamps=True,
            vad_filter=True,
            condition_on_previous_text=False,
        )
        log("info", f"Язык распознавания: {info.language}.")
        last_report = float("-inf")
        for segment in segments:
            now = monotonic()
            if now - last_report >= 10:
                position = min(duration, max(0, segment.end))
                log("info", f"Whisper дошёл до отметки {position:.0f} с из {duration:.0f} с записи.")
                last_report = now
            if segment.words:
                for word in segment.words:
                    start = min(duration, max(0.0, word.start))
                    yield Word(start, min(duration, max(start, word.end)), word.word)
            else:
                start = min(duration, max(0.0, segment.start))
                yield Word(start, min(duration, max(start, segment.end)), segment.text)
    except Exception as error:
        raise PipelineError("Whisper не смог расшифровать запись. Проверьте запись и параметр WHISPER_LANGUAGE.") from error


def process_media(
    path: Path, speaker_count: int | None, on_stage: Callable[[str], None], *,
    cpu_threads: int = 0, on_duration: Callable[[float], None] | None = None,
    on_event: ProgressLog | None = None,
) -> MeetingResult:
    def log(kind, message):
        if on_event is not None:
            on_event(kind, message)

    on_stage("decode")
    log("info", "Читаем звуковую дорожку и приводим её к моно 16 кГц.")
    samples = _decode(path)
    duration = len(samples) / SAMPLE_RATE
    if on_duration is not None:
        on_duration(round(duration, 3))
    log("done", f"Аудиодорожка готова. Длительность: {duration:.1f} с.")

    on_stage("diarize")
    turns, speakers = _diarize(samples, speaker_count, duration, log)
    log("done", f"Выделено голосов: {len(speakers)}. Интервалов речи: {len(turns)}.")

    on_stage("transcribe")
    segments = align_words(_whisper_words(samples, duration, cpu_threads, log), turns)
    if any(segment["speaker"] == UNKNOWN_SPEAKER for segment in segments):
        speakers[UNKNOWN_SPEAKER] = "Спикер не определён"
    log("done", f"Сверили временные метки слов и говорящих. Реплик в стенограмме: {len(segments)}.")

    on_stage("protocol")
    log("info", "Ищем в стенограмме темы, решения и поручения.")
    protocol = build_protocol(segments, speakers)
    log("done", f"Черновик готов. Тем: {len(protocol['topics'])}, решений: {len(protocol['decisions'])}, поручений: {len(protocol['tasks'])}.")
    return {
        "duration": round(duration, 3),
        "speakers": speakers,
        "segments": segments,
        "protocol": protocol,
    }
