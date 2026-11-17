"""HTTP API and durable background queue for meeting processing."""

import asyncio
import logging
import os
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from contextlib import asynccontextmanager
from math import ceil
from multiprocessing import get_context
from pathlib import Path
from shutil import copyfileobj
from time import time
from typing import Annotated, Literal
from urllib.parse import quote
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser
from starlette.requests import ClientDisconnect

from .demo import DEMO_FILENAME, DEMO_MEDIA, DEMO_TITLE, demo_result
from .exports import export_meeting
from .integrations import IntegrationError, integration_enabled, send_task
from .storage import Storage

LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "frontend" / "dist"
MEDIA_TYPES = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".flac": "audio/flac",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
}
MEBIBYTE = 1024 * 1024
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
TaskID = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
SpeakerName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Timecode = Annotated[float, Field(ge=0, allow_inf_nan=False)]


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: Text
    at: Timecode | None = None


class Task(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: TaskID
    title: Text
    assignee: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] = ""
    due_date: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] = ""
    at: Timecode | None = None


class Protocol(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(max_length=30000)
    topics: list[Text] = Field(max_length=200)
    decisions: list[Decision] = Field(max_length=200)
    tasks: list[Task] = Field(max_length=200)

    @field_validator("tasks")
    @classmethod
    def require_unique_task_ids(cls, tasks):
        if len({task.id for task in tasks}) != len(tasks):
            raise ValueError("Идентификаторы задач должны быть уникальными")
        return tasks


class MeetingEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    speakers: dict[str, SpeakerName] | None = Field(default=None, max_length=100)
    protocol: Protocol | None = None


class TaskSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_ids: list[TaskID] = Field(min_length=1, max_length=200)


class UploadTooLarge(MultiPartException):
    pass


class MeetingDeleted(Exception):
    pass


def process(storage, meeting, cpu_threads):
    from .pipeline import PipelineError, process_media

    meeting_id = meeting["id"]

    def report_stage(stage):
        if not storage.progress(meeting_id, stage):
            raise MeetingDeleted

    try:
        result = process_media(
            storage.media_directory / meeting["media_file"],
            meeting["speaker_count"],
            report_stage,
            cpu_threads=cpu_threads,
            on_duration=lambda duration: storage.set_duration(meeting_id, duration),
        )
        storage.complete(meeting_id, result)
    except MeetingDeleted:
        return
    except PipelineError as error:
        storage.fail(meeting_id, str(error))
    except Exception:
        LOGGER.exception("Обработка записи %s завершилась ошибкой", meeting_id)
        storage.fail(meeting_id, "Не удалось обработать запись. Подробности в журнале сервера.")


async def claim_work(state):
    # Keep one waiter responsible for clearing the event and checking SQLite.
    async with state.claim_lock:
        while not state.stopping:
            state.work_ready.clear()
            meeting = await run_in_threadpool(state.storage.claim)
            if meeting is not None:
                return meeting
            try:
                await asyncio.wait_for(state.work_ready.wait(), timeout=10)
            except TimeoutError:
                await run_in_threadpool(state.storage.purge_deleted)


async def run_worker(state, cpu_threads):
    loop = asyncio.get_running_loop()
    context = get_context("spawn")
    executor = ProcessPoolExecutor(max_workers=1, mp_context=context)
    try:
        while (meeting := await claim_work(state)) is not None:
            try:
                await loop.run_in_executor(executor, process, state.storage, meeting, cpu_threads)
            except Exception as error:
                LOGGER.exception("Не удалось завершить обработку записи %s", meeting["id"])
                if isinstance(error, BrokenProcessPool):
                    await run_in_threadpool(executor.shutdown, wait=True, cancel_futures=True)
                    executor = ProcessPoolExecutor(max_workers=1, mp_context=context)
                await run_in_threadpool(
                    state.storage.fail, meeting["id"],
                    "Процесс обработки завершился с ошибкой. Подробности в журнале сервера.",
                )
            finally:
                await run_in_threadpool(state.storage.release_deleted, meeting["id"])
    finally:
        await run_in_threadpool(executor.shutdown, wait=True, cancel_futures=True)


@asynccontextmanager
async def lifespan(app):
    data_directory = Path(os.getenv("MEETING_DATA_DIR", ROOT / "data")).resolve()
    max_upload_mb = int(os.getenv("MAX_UPLOAD_MB", "250"))
    if max_upload_mb < 1:
        raise ValueError("MAX_UPLOAD_MB должен быть положительным целым числом")
    worker_count = int(os.getenv("MEETING_WORKERS", "2"))
    if worker_count < 1:
        raise ValueError("MEETING_WORKERS должен быть положительным целым числом")
    cpu_threads = min(4, max(1, (os.cpu_count() or 1) // worker_count))
    app.state.storage = Storage(data_directory)
    app.state.storage.initialize()
    app.state.max_upload_mb = max_upload_mb
    app.state.work_ready = asyncio.Event()
    app.state.claim_lock = asyncio.Lock()
    app.state.stopping = False
    workers = [asyncio.create_task(run_worker(app.state, cpu_threads)) for _ in range(worker_count)]
    try:
        yield
    finally:
        app.state.stopping = True
        app.state.work_ready.set()
        await asyncio.gather(*workers)


app = FastAPI(title="Протокол совещаний", version="0.1.0", lifespan=lifespan)


def public_meeting(meeting, processing_rate=None):
    media_url = None
    if meeting["media_file"] or (meeting["demo"] and DEMO_MEDIA.is_file()):
        media_url = f"/api/meetings/{meeting['id']}/media"
    eta_seconds = None
    if meeting["status"] == "processing" and meeting["duration"] and meeting["processing_started_at"]:
        duration = meeting["duration"]
        expected = duration * processing_rate if processing_rate else 15 + duration * 0.75
        elapsed = max(0, time() - meeting["processing_started_at"])
        if expected > elapsed:
            eta_seconds = ceil(expected - elapsed)
    private_fields = {"media_file", "media_type", "speaker_count", "processing_started_at", "processing_seconds"}
    return {
        **{key: value for key, value in meeting.items() if key not in private_fields},
        "media_url": media_url,
        "eta_seconds": eta_seconds,
    }


def get_meeting(storage, meeting_id, *, completed=False):
    meeting = storage.get(meeting_id)
    if meeting is None:
        raise HTTPException(404, "Совещание не найдено.")
    if completed and meeting["status"] != "completed":
        raise HTTPException(409, "Дождитесь завершения обработки записи.")
    return meeting


async def read_form(request, max_bytes):
    content_type = request.headers.get("content-type", "").partition(";")[0].strip().lower()
    if content_type != "multipart/form-data":
        raise HTTPException(415, "Загрузите запись с помощью формы multipart/form-data.")

    # Permit bounded multipart framing and keep large media on disk while parsing.
    request_limit = max_bytes + 64 * 1024
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            body_size = int(content_length)
        except ValueError as error:
            raise HTTPException(400, "Неверно указан размер запроса.") from error
        if body_size > request_limit:
            raise HTTPException(413, "Размер записи превышает ограничение загрузки.")

    async def bounded_stream():
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > request_limit:
                raise UploadTooLarge("Размер записи превышает ограничение загрузки.")
            yield chunk

    parser = MultiPartParser(request.headers, bounded_stream(), max_files=1, max_fields=2, max_part_size=4096)
    try:
        return await parser.parse()
    except UploadTooLarge as error:
        raise HTTPException(413, error.message) from error
    except (MultiPartException, ClientDisconnect) as error:
        raise HTTPException(400, "Не удалось прочитать форму загрузки.") from error


def store_upload(storage, upload, title, speaker_count):
    filename = upload.filename.replace("\\", "/").rsplit("/", 1)[-1]
    suffix = Path(filename).suffix.lower()
    if suffix not in MEDIA_TYPES:
        raise HTTPException(415, "Неподдерживаемый формат записи.")
    if any(not character.isprintable() for character in filename) or len(filename) > 255:
        raise HTTPException(422, "Имя файла содержит недопустимые символы или слишком длинное.")
    meeting_id = uuid4().hex
    destination = storage.media_directory / f"{meeting_id}{suffix}"
    try:
        with destination.open("xb") as output:
            copyfileobj(upload.file, output, length=MEBIBYTE)
        return storage.add(
            meeting_id=meeting_id,
            title=title or Path(filename).stem[:200],
            filename=filename,
            media_file=destination.name,
            media_type=MEDIA_TYPES[suffix],
            speaker_count=speaker_count,
        )
    except Exception:
        destination.unlink(missing_ok=True)
        raise


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/config")
def config(request: Request):
    return {
        "max_upload_mb": request.app.state.max_upload_mb,
        "extensions": list(MEDIA_TYPES),
        "integration_enabled": integration_enabled(),
        "demo_enabled": True,
        "deletion_enabled": True,
    }


@app.get("/api/meetings")
def list_meetings(request: Request):
    storage = request.app.state.storage
    meetings = storage.list()
    rate = storage.processing_rate() if any(meeting["status"] == "processing" for meeting in meetings) else None
    return [public_meeting(meeting, rate) for meeting in meetings]


@app.post("/api/meetings", status_code=202)
async def upload_meeting(request: Request):
    state = request.app.state
    max_bytes = state.max_upload_mb * MEBIBYTE
    form = await read_form(request, max_bytes)
    try:
        upload = form.get("file")
        if not isinstance(upload, UploadFile) or not upload.filename or not upload.size:
            raise HTTPException(422, "Выберите непустой аудио- или видеофайл.")
        if upload.size > max_bytes:
            raise HTTPException(413, "Размер записи превышает ограничение загрузки.")
        title = form.get("title", "").strip()
        if len(title) > 200:
            raise HTTPException(422, "Название должно содержать не более 200 символов.")
        count = form.get("speaker_count", "").strip()
        if count and (not count.isascii() or not count.isdecimal() or len(count) > 2 or not 1 <= int(count) <= 20):
            raise HTTPException(422, "Укажите число участников от 1 до 20.")
        meeting = await run_in_threadpool(store_upload, state.storage, upload, title, int(count) if count else None)
    finally:
        await form.close()
    state.work_ready.set()
    return public_meeting(meeting)


@app.post("/api/demo", status_code=201)
def create_demo(request: Request):
    return public_meeting(request.app.state.storage.add(
        meeting_id=uuid4().hex,
        title=DEMO_TITLE,
        filename=DEMO_FILENAME,
        media_file=None,
        media_type="audio/wav",
        result=demo_result(),
    ))


@app.get("/api/meetings/{meeting_id}")
def detail(meeting_id: str, request: Request):
    storage = request.app.state.storage
    meeting = get_meeting(storage, meeting_id)
    rate = storage.processing_rate() if meeting["status"] == "processing" else None
    return {**public_meeting(meeting, rate), "deliveries": storage.deliveries(meeting_id)}


@app.delete("/api/meetings/{meeting_id}")
def delete_meeting(meeting_id: str, request: Request):
    storage = request.app.state.storage
    if storage.mark_for_deletion(meeting_id) == "sending":
        raise HTTPException(409, "Дождитесь завершения отправки поручений и повторите удаление.")
    storage.purge_deleted()
    return {"deleted": True}


@app.patch("/api/meetings/{meeting_id}")
def edit_meeting(meeting_id: str, edit: MeetingEdit, request: Request):
    storage = request.app.state.storage
    meeting = get_meeting(storage, meeting_id, completed=True)
    selected_speakers = edit.speakers
    if selected_speakers is not None:
        if selected_speakers.keys() - meeting["speakers"].keys():
            raise HTTPException(422, "Один из участников отсутствует в записи.")
    protocol = edit.protocol.model_dump() if edit.protocol is not None else None
    edited = storage.edit(meeting_id, selected_speakers, protocol)
    if edited is None:
        raise HTTPException(404, "Совещание не найдено.")
    updated = public_meeting(edited)
    return {**updated, "deliveries": storage.deliveries(meeting_id)}


@app.get("/api/meetings/{meeting_id}/media")
def get_media(meeting_id: str, request: Request):
    storage = request.app.state.storage
    meeting = get_meeting(storage, meeting_id)
    path = DEMO_MEDIA if meeting["demo"] else storage.media_directory / meeting["media_file"]
    if not path.is_file():
        raise HTTPException(404, "Файл записи недоступен.")
    return FileResponse(
        path,
        media_type=meeting["media_type"],
        filename=meeting["filename"],
        content_disposition_type="inline",
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"},
    )


@app.get("/api/meetings/{meeting_id}/export")
def download(meeting_id: str, request: Request, format: Literal["md", "csv", "json"] = "md"):
    meeting = public_meeting(get_meeting(request.app.state.storage, meeting_id, completed=True))
    data, mime, extension = export_meeting(meeting, format)
    filename = quote(f"{meeting['title']}.{extension}", safe="")
    return Response(data, media_type=mime, headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
        "Cache-Control": "private, no-store",
    })


@app.post("/api/meetings/{meeting_id}/tasks/send")
def deliver_tasks(meeting_id: str, selection: TaskSelection, request: Request):
    storage = request.app.state.storage
    meeting = public_meeting(get_meeting(storage, meeting_id, completed=True))
    if meeting["demo"]:
        raise HTTPException(409, "Задачи из примера не отправляются в корпоративную систему.")
    if not integration_enabled():
        raise HTTPException(409, "Интеграция с системой задач не настроена.")
    task_index = {task["id"]: task for task in meeting["protocol"]["tasks"]}
    sent, failed = [], []
    uncertain_message = "Отправка могла создать задачу. Проверьте её в системе задач."
    for task_id in dict.fromkeys(selection.task_ids):
        task = task_index.get(task_id)
        if task is None:
            failed.append({"task_id": task_id, "error": "Задача отсутствует в протоколе."})
            continue
        previous = storage.reserve_task(meeting_id, task_id)
        if previous is not None:
            if previous["status"] == "unavailable":
                raise HTTPException(404, "Совещание не найдено.")
            if previous["status"] == "sent":
                sent.append({"task_id": task_id, "external_id": previous["external_id"], "url": previous["url"]})
            else:
                error = "Задача уже отправляется." if previous["status"] == "sending" else uncertain_message
                failed.append({"task_id": task_id, "error": error})
            continue
        try:
            receipt = send_task(meeting, task)
        except IntegrationError as error:
            storage.fail_task(meeting_id, task_id, uncertain=error.uncertain)
            message = f"{error} {uncertain_message}" if error.uncertain else str(error)
            failed.append({"task_id": task_id, "error": message})
        except Exception:
            LOGGER.exception("Ошибка отправки задачи %s совещания %s", task_id, meeting_id)
            storage.fail_task(meeting_id, task_id, uncertain=True)
            failed.append({"task_id": task_id, "error": uncertain_message})
        else:
            storage.complete_task(meeting_id, task_id, receipt)
            sent.append({"task_id": task_id, **receipt})
    return {"sent": sent, "failed": failed}


if (DIST / "index.html").is_file():
    app.mount("/", StaticFiles(directory=DIST, html=True), name="frontend")
