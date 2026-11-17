"""Downloadable meeting minutes and a spreadsheet-friendly task list."""

import csv
import io
import json
import re


def timestamp(value: float | None) -> str:
    if value is None:
        return "—"
    minutes, seconds = divmod(max(0, int(value)), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _cell(value: str) -> str:
    if value.startswith(("\t", "\r", "\n")) or value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def _md(value: str) -> str:
    return re.sub(r"([\\`*_{}\[\]()#+.!|>~\-])", r"\\\1", value.replace("<", "&lt;"))


def export_meeting(meeting: dict, format: str) -> tuple[bytes, str, str]:
    protocol = meeting["protocol"]
    if format == "json":
        fields = ("id", "title", "filename", "created_at", "duration", "speakers", "segments", "protocol")
        content = {field: meeting[field] for field in fields}
        return json.dumps(content, ensure_ascii=False, indent=2).encode(), "application/json", "json"
    if format == "csv":
        output = io.StringIO(newline="")
        rows = csv.writer(output, delimiter=";")
        rows.writerow(["Задача", "Исполнитель", "Срок", "Метка в записи"])
        for task in protocol["tasks"]:
            rows.writerow([*(_cell(task.get(field) or "") for field in ("title", "assignee", "due_date")), timestamp(task.get("at"))])
        return output.getvalue().encode("utf-8-sig"), "text/csv", "csv"
    if format != "md":
        raise ValueError("Неизвестный формат выгрузки.")
    lines = [f"# {_md(meeting['title'])}", "", "## Краткий итог", "", _md(protocol["summary"]), ""]
    lines.extend(["## Темы", "", *[f"- {_md(item)}" for item in protocol["topics"]], ""])
    lines.extend(["## Решения", ""])
    lines.extend(f"- {timestamp(item['at'])} · {_md(item['text'])}" for item in protocol["decisions"])
    lines.extend(["", "## Поручения", ""])
    for task in protocol["tasks"]:
        fields = [_md(task["title"]), _md(task["assignee"] or "Исполнитель не указан"), _md(task["due_date"] or "Срок не указан")]
        lines.append(f"- {timestamp(task['at'])} · " + " · ".join(fields))
    lines.extend(["", "## Стенограмма", ""])
    for segment in meeting["segments"]:
        speaker = meeting["speakers"].get(segment["speaker"], segment["speaker"])
        lines.append(f"- {timestamp(segment['start'])} → {timestamp(segment['end'])} · **{_md(speaker)}** · {_md(segment['text'])}")
    return ("\n".join(lines) + "\n").encode(), "text/markdown", "md"
