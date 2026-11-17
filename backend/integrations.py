"""Deliver selected action items using one server-configured adapter."""

import hashlib
import json
import os
from datetime import date, datetime, time, timezone
from html import escape
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


YOUGILE_ENDPOINT = "https://yougile.com/api-v2/tasks"


class IntegrationError(RuntimeError):
    def __init__(self, message: str, *, uncertain: bool = False):
        super().__init__(message)
        self.uncertain = uncertain


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _web_url(value: str) -> str:
    try:
        parts = urlsplit(value)
        if parts.scheme in {"http", "https"} and parts.hostname and not parts.username and not parts.password:
            return value
    except ValueError:
        pass
    return ""


def integration_enabled() -> bool:
    webhook = os.getenv("TASK_WEBHOOK_URL", "").strip()
    if webhook:
        return bool(_web_url(webhook))
    return bool(os.getenv("YOUGILE_API_KEY", "").strip() and os.getenv("YOUGILE_COLUMN_ID", "").strip())


def _request(url: str, payload: dict, headers: dict, *, can_retry: bool) -> dict:
    request = Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json", **headers},
    )
    try:
        with build_opener(NoRedirect()).open(request, timeout=12) as response:
            result = json.loads(response.read(65_537))
    except HTTPError as exc:
        raise IntegrationError(
            f"Система задач вернула HTTP {exc.code}. Проверьте настройки и журнал трекера.",
            uncertain=not can_retry and (exc.code >= 500 or exc.code == 408),
        ) from exc
    except (URLError, OSError) as exc:
        raise IntegrationError("Связь с системой задач прервалась. Проверьте результат в трекере.", uncertain=not can_retry) from exc
    except (ValueError, UnicodeError) as exc:
        raise IntegrationError("Система задач не вернула подтверждение в формате JSON.", uncertain=not can_retry) from exc
    if not isinstance(result, dict):
        raise IntegrationError("Система задач не вернула карточку созданной задачи.", uncertain=not can_retry)
    return result


def send_task(meeting: dict, task: dict) -> dict:
    if not integration_enabled():
        raise IntegrationError("Подключите YouGile или вебхук в настройках сервера.")
    source = f"{meeting['id']}:{task['id']}"
    unique = "protokol-" + hashlib.sha256(source.encode()).hexdigest()[:38]
    assignee = (task.get("assignee") or "").strip()
    deadline = (task.get("due_date") or "").strip()
    webhook = os.getenv("TASK_WEBHOOK_URL", "").strip()
    if webhook:
        headers = {"Idempotency-Key": unique}
        token = os.getenv("TASK_WEBHOOK_TOKEN", "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        result = _request(webhook, {
            "event": "meeting.task.create", "idempotency_key": unique,
            "meeting": {"id": meeting["id"], "title": meeting["title"]},
            "task": {key: task[key] for key in ("id", "title", "assignee", "due_date", "at")},
        }, headers, can_retry=False)
        key = result.get("external_id")
        url = _web_url(result.get("url") if isinstance(result.get("url"), str) else "")
    else:
        notes = [f"Совещание: {meeting['title']}"]
        if task.get("at") is not None:
            minutes, seconds = divmod(max(0, int(task["at"])), 60)
            notes.append(f"Метка в записи: {minutes:02d}:{seconds:02d}")
        if assignee:
            notes.append(f"Исполнитель по протоколу: {assignee}")
        payload = {
            "columnId": os.environ["YOUGILE_COLUMN_ID"].strip(),
            "title": task["title"],
            "description": "".join(f"<p>{escape(note)}</p>" for note in notes),
            "idempotencyKey": unique,
        }
        if deadline:
            try:
                date_value = date.fromisoformat(deadline)
            except ValueError as exc:
                raise IntegrationError("Укажите срок в формате ГГГГ-ММ-ДД.") from exc
            payload["deadline"] = {
                "deadline": int(datetime.combine(date_value, time(12), timezone.utc).timestamp() * 1000),
                "withTime": False,
                "blockedPoints": [],
                "links": [],
            }
        try:
            user_ids = json.loads(os.getenv("YOUGILE_ASSIGNEES", "{}"))
        except ValueError as exc:
            raise IntegrationError("YOUGILE_ASSIGNEES должен содержать JSON-словарь имён и ID.") from exc
        if not isinstance(user_ids, dict) or not all(isinstance(v, str) for v in user_ids.values()):
            raise IntegrationError("YOUGILE_ASSIGNEES должен содержать JSON-словарь имён и ID.")
        if assignee and user_ids.get(assignee):
            payload["assigned"] = [user_ids[assignee]]
        result = _request(YOUGILE_ENDPOINT, payload, {
            "Authorization": f"Bearer {os.environ['YOUGILE_API_KEY'].strip()}",
        }, can_retry=True)
        key = result.get("id")
        url = ""
    if isinstance(key, bool) or not isinstance(key, (str, int)) or not str(key).strip():
        raise IntegrationError("Система задач не вернула идентификатор карточки.", uncertain=bool(webhook))
    return {"external_id": str(key), "url": url}
