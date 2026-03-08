from __future__ import annotations

import json
import re

from .models import CommentBatch, VALID_MOODS


CODE_BLOCK_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)
THINKING_RE = re.compile(r"<think>[\s\S]*?</think>", re.IGNORECASE)
SPECIAL_TAG_RE = re.compile(r"<\|[^|]*\|>[^<]*")
NUMBERED_PREFIX_RE = re.compile(r"^\d+[.):\s]+")
EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001F9FF"
    "\u2600-\u27BF"
    "\uFE00-\uFE0F"
    "\u200D"
    "]+",
    re.UNICODE,
)


def parse_comment_batch(text: str) -> CommentBatch:
    cleaned = _clean_thinking_tags(text).strip()
    if not cleaned:
        return CommentBatch(comments=[])

    fenced_match = CODE_BLOCK_RE.search(cleaned)
    if fenced_match:
        cleaned = fenced_match.group(1).strip()

    try:
        return _extract_from_json(json.loads(cleaned))
    except (json.JSONDecodeError, TypeError):
        pass

    first_brace = cleaned.find("{")
    last_brace = cleaned.rfind("}")
    if 0 <= first_brace < last_brace:
        snippet = cleaned[first_brace : last_brace + 1]
        try:
            return _extract_from_json(json.loads(snippet))
        except (json.JSONDecodeError, TypeError):
            pass

    return _parse_line_batch(cleaned)


def _extract_from_json(raw: dict) -> CommentBatch:
    comments = []
    for line in raw.get("comments", []):
        if not isinstance(line, str):
            continue
        cleaned = clean_comment_line(line)
        if cleaned:
            comments.append(cleaned[:40])

    mood = str(raw.get("mood", "general")).lower().strip()
    if mood not in VALID_MOODS:
        mood = "general"

    excitement_value = raw.get("excitement", 5)
    try:
        excitement = max(1, min(10, int(excitement_value)))
    except (TypeError, ValueError):
        excitement = 5

    return CommentBatch(comments=comments, mood=mood, excitement=excitement)


def _parse_line_batch(text: str) -> CommentBatch:
    lines = [clean_comment_line(line) for line in text.splitlines()]
    lines = [line for line in lines if line]

    mood = "general"
    if lines:
        tail = lines[-1].lower().replace("mood:", "").replace("mood", "").strip()
        if tail in VALID_MOODS:
            mood = tail
            lines.pop()

    comments = []
    for line in lines:
        trimmed = line[:40]
        if len(trimmed) < 1:
            continue
        if _looks_like_json_fragment(trimmed):
            continue
        if _is_repetitive(trimmed):
            continue
        comments.append(trimmed)
    return CommentBatch(comments=comments, mood=mood)


def clean_comment_line(line: str) -> str:
    result = NUMBERED_PREFIX_RE.sub("", line.strip())
    if result.startswith("- "):
        result = result[2:]

    result = result.replace("。", "").replace("、", "").replace("！", "")
    result = EMOJI_RE.sub("", result)
    if result.startswith(("*", "#")):
        return ""
    return result.strip()


def _clean_thinking_tags(text: str) -> str:
    return SPECIAL_TAG_RE.sub("", THINKING_RE.sub("", text))


def _looks_like_json_fragment(text: str) -> bool:
    candidate = text.strip()
    if candidate in {"{", "}", "[", "]"}:
        return True
    if candidate.startswith("\"") and ":" in candidate:
        return True
    if candidate.startswith("{\"") or candidate.startswith("[\""):
        return True
    if candidate.endswith(",") and "\"" in candidate and ":" in candidate:
        return True
    return False


def _is_repetitive(text: str) -> bool:
    if len(text) < 4:
        return False
    first = text[0]
    same_count = sum(1 for char in text if char == first)
    return same_count / len(text) > 0.8
