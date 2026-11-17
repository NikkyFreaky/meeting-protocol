"""Extract a reviewable Russian meeting protocol from the timestamped transcript."""

import re
from collections import Counter
from dataclasses import dataclass
from datetime import date
from typing import TypedDict


class TranscriptSegment(TypedDict):
    start: float
    end: float
    speaker: str
    text: str


class Decision(TypedDict):
    text: str
    at: float


class Task(TypedDict):
    id: str
    title: str
    assignee: str
    due_date: str
    at: float


class Protocol(TypedDict):
    summary: str
    topics: list[str]
    decisions: list[Decision]
    tasks: list[Task]


@dataclass(frozen=True)
class Passage:
    text: str
    speaker: str
    at: float


DECISION = re.compile(
    r"^(?:итак[, ]+|по итогам[,: ]+)?(?:мы\s+)?"
    r"(?:решили|решено|договорились|пришли\s+к\s+решению|утвердили|утверждаем|согласовали|одобрили|зафиксировали)\b",
    re.IGNORECASE,
)
TOPIC = re.compile(r"\b(?:обсудим|обсудили|обсуждаем|на\s+повестке|следующий\s+вопрос|перейд[её]м\s+к)\b", re.IGNORECASE)
ACTION = (
    r"(?:подготов\w*|отправ\w*|пришл\w*|пришлите|провери\w*|проверь\w*|сдела\w*|сделай\w*|"
    r"доработа\w*|обнов\w*|созда\w*|собер\w*|собрать|согласу\w*|согласова\w*|"
    r"добав\w*|переда\w*|уточн\w*|запланиру\w*|подел\w*|опублику\w*|"
    r"предостав\w*|направ\w*|заверш\w*|исправ\w*|разработа\w*)"
)
TASK_REQUEST = re.compile(r"\b(?:поручаю|поручили|поручить|прошу|надо|нужно|необходимо|задача)\b", re.IGNORECASE)
TASK_ACTION = re.compile(rf"\b{ACTION}\b", re.IGNORECASE)
THIRD_PERSON = re.compile(r"(?:ет|ит|ёт|ут|ют|ят|ат)$", re.IGNORECASE)
IMPERATIVE = re.compile(r"(?:й|йте|и|ите|ь|ьте)$", re.IGNORECASE)
SELF_TASK = re.compile(r"\bя\s+(?:(?:тогда|также|ещё|еще)\s+)?(?:возьму\s+на\s+себя|беру\s+на\s+себя|подготовлю|отправлю|пришлю|"
                       r"проверю|сделаю|доработаю|обновлю|создам|соберу|согласую|добавлю|передам|уточню|"
                       r"запланирую|предоставлю|направлю|завершу|исправлю|разработаю)\b", re.IGNORECASE)
NAME = r"[А-ЯЁ][а-яё-]{1,30}(?:\s+[А-ЯЁ][а-яё-]{1,30})?"
NAMED_TASK = re.compile(rf"^(?P<name>{NAME})(?P<request>,\s+(?:пожалуйста,\s+)?|\s+)(?P<action>{ACTION})\b")
RESPONSIBLE = re.compile(rf"\b(?:[Оо]тветственн(?:ый|ая)|[Ии]сполнитель)\s*[:—–-]?\s*(?P<name>{NAME})\b")
ASSIGNMENT_PREFIX = re.compile(r"^(?:договорились|решили|задача)\s*[:—–-]\s*", re.IGNORECASE)
NEGATION = re.compile(r"\b(?:не\s+(?:надо|нужно|будем)|отменили|отменяется)\b", re.IGNORECASE)
NON_NAMES = set("команда отдел группа руководство организация компания завтра сегодня вместе потом итак".split())
MONTHS = "января февраля марта апреля мая июня июля августа сентября октября ноября декабря".split()
DEADLINE = re.compile(
    r"\b(?:до|к|не\s+позднее|срок\s*[:—–-]?)\s+(?:"
    r"(?P<iso>\d{4}-\d{2}-\d{2})|"
    r"(?P<day>\d{1,2})[./](?P<month>\d{1,2})[./](?P<year>\d{4})|"
    rf"(?P<word_day>\d{{1,2}})\s+(?P<word_month>{'|'.join(MONTHS)})\s+(?P<word_year>\d{{4}}))\b",
    re.IGNORECASE,
)
WORD = re.compile(r"[а-яёa-z]{3,}", re.IGNORECASE)
STOPWORDS = set("это как так для или что вот еще уже все при нас они был была были быть тоже "
                "очень чтобы если когда тогда надо можно нужно будет здесь пока сюда там чем "
                "этот этой того такой самое нашем этот сейчас потом просто сегодня давайте "
                "коллеги добрый день привет спасибо хорошо решили да нет обсудили".split())


def _words(text: str) -> set[str]:
    return set(WORD.findall(text.casefold())) - STOPWORDS


def _passages(segments: list[TranscriptSegment]) -> list[Passage]:
    passages: list[Passage] = []
    seen: set[str] = set()
    for segment in segments:
        clean = " ".join(segment["text"].split())
        for text in re.split(r"(?<=[.!?])\s+(?=[А-ЯЁA-Z0-9«])", clean):
            key = text.casefold().strip(" .!?")
            if _words(text) and key not in seen:
                passages.append(Passage(text=text, speaker=segment["speaker"], at=segment["start"]))
                seen.add(key)
    return passages


def _deadline(text: str) -> str:
    match = DEADLINE.search(text)
    if not match:
        return ""
    try:
        if match["iso"]:
            return date.fromisoformat(match["iso"]).isoformat()
        if match["day"]:
            return date(int(match["year"]), int(match["month"]), int(match["day"])).isoformat()
        month = MONTHS.index(match["word_month"].casefold()) + 1
        return date(int(match["word_year"]), month, int(match["word_day"])).isoformat()
    except ValueError:
        return ""


def _task_title_and_owner(passage: Passage, speakers: dict[str, str]) -> tuple[str, str] | None:
    text = passage.text
    if text.endswith("?") or NEGATION.search(text):
        return None

    action_text = ASSIGNMENT_PREFIX.sub("", text)
    self_task = SELF_TASK.search(text)
    if self_task:
        assignee = speakers.get(passage.speaker, "") if passage.speaker != "unknown" else ""
        return text, assignee

    named = NAMED_TASK.match(action_text)
    if named and named["name"].casefold() not in NON_NAMES:
        endings = IMPERATIVE if named["request"].startswith(",") else THIRD_PERSON
        if endings.search(named["action"]):
            return text, named["name"]

    responsible = RESPONSIBLE.search(text)
    if TASK_ACTION.search(text) and (TASK_REQUEST.search(text) or responsible):
        assignee = responsible["name"] if responsible else ""
        return text, assignee
    return None


def build_protocol(segments: list[TranscriptSegment], speakers: dict[str, str]) -> Protocol:
    passages = _passages(segments)
    if not passages:
        return {"summary": "В записи не удалось распознать речь.", "topics": [], "decisions": [], "tasks": []}

    decisions: list[Decision] = []
    tasks: list[Task] = []
    discussion: list[Passage] = []
    topic_passages: list[Passage] = []
    for passage in passages:
        text = passage.text
        task = _task_title_and_owner(passage, speakers)
        is_decision = DECISION.search(text) and not text.endswith("?")
        if is_decision:
            decisions.append({"text": text, "at": passage.at})
        if task:
            title, assignee = task
            tasks.append({
                "id": f"task-{len(tasks) + 1}",
                "title": title,
                "assignee": assignee,
                "due_date": _deadline(text),
                "at": passage.at,
            })
        if not is_decision and not task:
            discussion.append(passage)
            if TOPIC.search(text):
                topic_passages.append(passage)

    # Score passages by lightly favoring terms that recur across the meeting.
    tokens = [_words(passage.text) for passage in passages]
    frequencies = Counter(word for words in tokens for word in words)
    ranks = sorted(range(len(passages)), key=lambda index: -sum(
        min(frequencies[word], 4) for word in tokens[index]
    ) / max(6, len(tokens[index])))
    summary = " ".join(passages[index].text for index in sorted(ranks[:3]))
    if not topic_passages:
        discussion_set = set(discussion)
        topic_passages = [passages[index] for index in ranks if passages[index] in discussion_set]

    return {
        "summary": summary,
        "topics": [passage.text for passage in sorted(topic_passages[:5], key=lambda item: item.at)],
        "decisions": decisions,
        "tasks": tasks,
    }
