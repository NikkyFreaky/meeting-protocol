"""A short synthetic meeting with a reviewed transcript and minutes."""

import json
from pathlib import Path


ASSETS = Path(__file__).with_name("assets")
DEMO_MEDIA = ASSETS / "demo.wav"
DEMO_TITLE = "Планёрка по запуску пилота"
DEMO_FILENAME = "pilot-meeting.wav"


def demo_result() -> dict:
    result = json.loads((ASSETS / "demo.json").read_text(encoding="utf-8"))
    segments = result["segments"]
    result["speakers"] = {"S1": "Ирина", "S2": "Павел"}
    result["protocol"] = {
        "summary": "Команда согласовала пилот на трёх записях проектной команды. Перед переносом в YouGile участники проверят поручения в протоколе. Результаты обсудят на следующей планёрке.",
        "topics": ["Подготовка записей для пилота", "Контроль диаризации и поручений", "Проверка переноса задач в YouGile"],
        "decisions": [
            {"text": "Начать пилот с проектной команды.", "at": segments[2]["start"]},
            {"text": "Проверять каждую задачу перед отправкой в трекер.", "at": segments[4]["start"]},
        ],
        "tasks": [
            {"id": "demo-prepare", "title": "Подготовить три записи для пилота", "assignee": "Павел", "due_date": "2026-11-20", "at": segments[2]["start"]},
            {"id": "demo-feedback", "title": "Собрать замечания участников", "assignee": "Павел", "due_date": "", "at": segments[3]["start"]},
            {"id": "demo-export", "title": "Проверить выгрузку задач в YouGile", "assignee": "Ирина", "due_date": "2026-11-24", "at": segments[5]["start"]},
        ],
    }
    return result
