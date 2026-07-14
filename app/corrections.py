"""Automatic domain correction learning from user edits.

The correction library is intentionally conservative:
- every summary save records the before/after text as an edit event;
- short replace-like edits become candidate correction rules;
- a rule is only auto-enabled after repeated hits;
- enabled rules are applied locally to transcript clean_text before summarization.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from typing import Any, Dict, Iterable, List, Optional

from . import db
from .models import MeetingSummary, Segment

AUTO_ENABLE_HITS = 3
MAX_CANDIDATES_PER_EDIT = 20
MAX_RULES_PER_TEXT = 50
_TRIM_CHARS = " \t\r\n，。！？；：、,.!?;:()（）[]【】\"'“”‘’"
_CONNECTORS = set("和与及或跟同、，。！？；：,.!?;:()（）[]【】")
_SPEAKER_LABEL_RE = re.compile(r"\bSPEAKER_\d+\b", re.IGNORECASE)


def summary_to_text(summary: Optional[MeetingSummary]) -> str:
    if not summary:
        return ""
    parts: List[str] = [
        summary.title or "",
        summary.summary or "",
    ]
    for section in summary.sections or []:
        parts.extend([section.title or "", section.content or ""])
    for item in summary.topics or []:
        parts.extend([item.title or "", item.summary or ""])
    for item in summary.decisions or []:
        parts.append(item.content or "")
    for item in summary.todos or []:
        parts.extend([item.owner or "", item.task or "", item.deadline or ""])
    for item in summary.risks or []:
        parts.append(item.content or "")
    for item in summary.open_questions or []:
        parts.append(item.content or "")
    return "\n".join(p for p in parts if p).strip()


def learn_from_summary_edit(
    meeting_id: str,
    before: Optional[MeetingSummary],
    after: MeetingSummary,
    protected_terms: Optional[Iterable[str]] = None,
) -> List[Dict[str, Any]]:
    before_text = summary_to_text(before)
    after_text = summary_to_text(after)
    return learn_from_text_edit(
        meeting_id,
        before_text,
        after_text,
        protected_terms=protected_terms,
    )


def learn_from_text_edit(
    meeting_id: str,
    before_text: str,
    after_text: str,
    *,
    edit_path: Optional[str] = None,
    protected_terms: Optional[Iterable[str]] = None,
) -> List[Dict[str, Any]]:
    """Learn correction candidates from one concrete edited field."""
    if not before_text or before_text == after_text:
        return []

    protected = _normalize_protected_terms(protected_terms)
    candidates = extract_candidates(before_text, after_text)
    candidates = [
        c for c in candidates
        if not _is_protected_candidate(c["wrong_text"], c["correct_text"], protected)
    ]
    if not candidates:
        db.save_correction_event(meeting_id, before_text, after_text, [])
        return []

    learned: List[Dict[str, Any]] = []
    for c in candidates[:MAX_CANDIDATES_PER_EDIT]:
        rule = db.upsert_correction_rule(
            c["wrong_text"],
            c["correct_text"],
            confidence=c["confidence"],
            auto_enable_hits=AUTO_ENABLE_HITS,
            example={
                "meeting_id": meeting_id,
                "edit_path": edit_path or "",
                "before": c["wrong_text"],
                "after": c["correct_text"],
            },
        )
        learned.append(rule)
    db.save_correction_event(meeting_id, before_text, after_text, candidates)
    return learned


def extract_candidates(before_text: str, after_text: str) -> List[Dict[str, Any]]:
    before_norm = _compact_space(before_text)
    after_norm = _compact_space(after_text)
    candidates = _same_length_candidates(before_norm, after_norm)
    seen = {(c["wrong_text"], c["correct_text"]) for c in candidates}
    matcher = difflib.SequenceMatcher(None, before_norm, after_norm, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "replace":
            continue
        wrong = _clean_edge(before_norm[i1:i2])
        correct = _clean_edge(after_norm[j1:j2])
        if not _is_candidate(wrong, correct):
            continue
        key = (wrong, correct)
        if key in seen:
            continue
        seen.add(key)
        candidates.append({
            "wrong_text": wrong,
            "correct_text": correct,
            "confidence": _candidate_confidence(wrong, correct),
        })
    candidates.sort(key=lambda x: (x["confidence"], len(x["wrong_text"])), reverse=True)
    return candidates


def _same_length_candidates(before_text: str, after_text: str) -> List[Dict[str, Any]]:
    if len(before_text) != len(after_text):
        return []
    diff_positions = [i for i, (a, b) in enumerate(zip(before_text, after_text)) if a != b]
    if not diff_positions:
        return []

    groups: List[List[int]] = []
    cur = [diff_positions[0]]
    for pos in diff_positions[1:]:
        gap_text = before_text[cur[-1] + 1:pos]
        if pos - cur[-1] <= 3 and not any(ch in _CONNECTORS for ch in gap_text):
            cur.append(pos)
        else:
            groups.append(cur)
            cur = [pos]
    groups.append(cur)

    candidates: List[Dict[str, Any]] = []
    for group in groups:
        start, end = group[0], group[-1] + 1
        wrong = _clean_edge(before_text[start:end])
        correct = _clean_edge(after_text[start:end])
        if _is_candidate(wrong, correct):
            candidates.append({
                "wrong_text": wrong,
                "correct_text": correct,
                "confidence": _candidate_confidence(wrong, correct),
            })
    return candidates


def apply_enabled_rules_to_segments(segments: Iterable[Segment]) -> int:
    changed = 0
    rules = db.get_enabled_correction_rules()
    for seg in segments:
        corrected, n = apply_rules(seg.text, rules)
        if n:
            seg.text = corrected
            changed += n
    return changed


def apply_rules(text: str, rules: List[Dict[str, Any]]) -> tuple[str, int]:
    if not text or not rules:
        return text, 0
    out = text
    changed = 0
    for rule in rules[:MAX_RULES_PER_TEXT]:
        wrong = (rule.get("wrong_text") or "").strip()
        correct = (rule.get("correct_text") or "").strip()
        if not wrong or not correct or wrong == correct:
            continue
        if wrong in out:
            count = out.count(wrong)
            out = out.replace(wrong, correct)
            changed += count
    return out, changed


def matching_enabled_rules(text: str, limit: int = 20) -> List[Dict[str, Any]]:
    if not text:
        return []
    matches = []
    for rule in db.get_enabled_correction_rules():
        wrong = rule.get("wrong_text") or ""
        if wrong and wrong in text:
            matches.append(rule)
            if len(matches) >= limit:
                break
    return matches


def _compact_space(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _clean_edge(text: str) -> str:
    return (text or "").strip(_TRIM_CHARS)


def _normalize_protected_terms(terms: Optional[Iterable[str]]) -> set[str]:
    out: set[str] = set()
    for term in terms or []:
        cleaned = _comparison_form(str(term or ""))
        # A one-character display name (for example, "王") can still be
        # embedded in a longer replacement candidate such as "王总".
        if cleaned:
            out.add(cleaned)
    return out


def _is_protected_candidate(wrong: str, correct: str, protected_terms: set[str]) -> bool:
    if _SPEAKER_LABEL_RE.search(wrong or "") or _SPEAKER_LABEL_RE.search(correct or ""):
        return True
    wrong = _comparison_form(wrong)
    correct = _comparison_form(correct)
    for term in protected_terms:
        if term in wrong or term in correct or wrong in term or correct in term:
            return True
    return False


def _comparison_form(text: str) -> str:
    """Normalize terms before containment checks without changing saved text."""
    return unicodedata.normalize("NFKC", _clean_edge(text)).casefold()


def _is_candidate(wrong: str, correct: str) -> bool:
    if not wrong or not correct or wrong == correct:
        return False
    if len(wrong) < 2 or len(correct) < 2:
        return False
    if len(wrong) > 24 or len(correct) > 24:
        return False
    if "\n" in wrong or "\n" in correct:
        return False
    if wrong.isdigit() or correct.isdigit():
        return False
    if _looks_like_sentence(wrong) or _looks_like_sentence(correct):
        return False
    ratio = max(len(wrong), len(correct)) / max(1, min(len(wrong), len(correct)))
    if ratio > 2.5:
        return False
    return True


def _looks_like_sentence(text: str) -> bool:
    return len(re.findall(r"[，。！？；,.!?;]", text)) >= 2


def _candidate_confidence(wrong: str, correct: str) -> float:
    similarity = difflib.SequenceMatcher(None, wrong, correct, autojunk=False).ratio()
    len_ratio = min(len(wrong), len(correct)) / max(len(wrong), len(correct))
    return round((similarity * 0.7) + (len_ratio * 0.3), 3)
