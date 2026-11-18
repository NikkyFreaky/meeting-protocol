"""Prepare model weights with python -m backend.setup_models."""

import os
from pathlib import Path


def model_cache_dir() -> Path:
    configured = os.getenv("MEETING_MODEL_DIR", "").strip()
    return Path(configured).expanduser().resolve() if configured else Path(__file__).resolve().parents[1] / ".models"


def whisper_model_name() -> str:
    return os.getenv("WHISPER_MODEL", "small").strip() or "small"


def download_diarization_models(cache_dir: Path) -> tuple[str, str]:
    os.environ.setdefault("HF_XET_CACHE", str(cache_dir / "xet"))
    from huggingface_hub import hf_hub_download

    segmentation = hf_hub_download(
        repo_id="csukuangfj/sherpa-onnx-pyannote-segmentation-3-0",
        filename="model.onnx",
        cache_dir=cache_dir,
        token=False,
    )
    embedding = hf_hub_download(
        repo_id="csukuangfj/speaker-embedding-models",
        filename="3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx",
        cache_dir=cache_dir,
        token=False,
    )
    return segmentation, embedding


def main() -> None:
    from .pipeline import _diarization_backend, _load_pyannote, _pyannote_device

    cache_dir = model_cache_dir()
    print("Загрузка моделей диаризации…", flush=True)
    if _diarization_backend() == "sherpa":
        download_diarization_models(cache_dir)
    else:
        _load_pyannote(cache_dir, _pyannote_device())

    model_name = whisper_model_name()
    print(f"Подготовка Whisper ({model_name})…", flush=True)
    if not Path(model_name).is_dir():
        from faster_whisper.utils import download_model

        download_model(model_name, cache_dir=str(cache_dir), use_auth_token=False)

    print(f"Модели готовы. Кеш: {cache_dir}")


if __name__ == "__main__":
    main()
