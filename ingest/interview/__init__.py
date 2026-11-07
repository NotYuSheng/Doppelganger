"""Persona interview (issue #14, phase 2).

A semi-structured interview about the person: who they are, their work, the
people in their life, their week, interests, opinions, values, how they text,
and a recent story. It's modelled on life-story interviews: agents built from
two-hour interview transcripts replicated people's survey answers 85% as well
as the people did themselves two weeks later (Park et al. 2024,
arXiv:2411.10109).

It produces two things:

* **Training pairs**: each interviewer question plus the answer, in the user's
  own words. Saved to ``data/elicited.jsonl`` (``kind: "interview"``) next to
  the reply-drill answers, and read by the ``elicited`` source.
* **A persona card** (``data/persona.md``): a short, static profile of who the
  person is (background, work, people, interests, opinions, values) from the
  transcript, plus a texting-style section measured from their own answers. It
  becomes the system prompt for ``chat`` and can be scored with ``eval generate
  --persona``. Fine-tuning learns *how* you write; the card gives the model a
  picture of *who* you are.

  The card is a profile, not a memory: it doesn't let the model look up
  specific past conversations or events. That's retrieval-augmented memory,
  issue #16.

With an LLM (local by default), follow-up questions react to the answers and
the card is written from the transcript. Without one, fixed follow-ups are
used and the card lists the answers by topic, so the interview still works
offline.
"""

import json
import os
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence

from ingest import stylometry
from ingest.drills import DEFAULT_PATH as ELICITED_PATH, load_answers as load_drill_answers

PERSONA_PATH = os.path.join("data", "persona.md")
SECONDS_PER_QUESTION = 60  # rough time to read a question and type an answer


@dataclass(frozen=True)
class Topic:
    id: str
    label: str
    question: str               # the opening question
    follow_ups: Sequence[str]   # used when no LLM is available
    focus: str                  # what follow-ups should dig for (guides the LLM)


TOPICS: List[Topic] = [
    Topic("intro", "About you",
          "To start: what should I call you, and how would you describe yourself in a couple of sentences?",
          ["How do you think your friends would describe you?",
           "What's something about you that people usually don't guess?"],
          "name, age range or life stage, personality in their own words"),
    Topic("work", "Work or study",
          "What do you spend your days doing: work, study, or something else? What's it actually like?",
          ["What's the best and the most annoying part of it?",
           "How would you explain what you do to someone who's never heard of it?"],
          "what they do, what they like and dislike, jargon they use"),
    Topic("people", "The people in your life",
          "Who are the people you talk to most? Tell me a bit about them.",
          ["How is the way you talk to your family different from your friends or colleagues?",
           "Who do you text the most, and what do you usually talk about?"],
          "key relationships, names and roles, how their tone changes per person"),
    Topic("week", "Where you live and your week",
          "Where do you live, and what does a normal week look like for you?",
          ["What's your favourite part of the week?",
           "What do you usually do to unwind?"],
          "place, routine, habits, favourite spots"),
    Topic("interests", "Interests",
          "What are you into lately: hobbies, shows, music, games, food, anything?",
          ["How did you get into that?",
           "What would you recommend to a friend right now, and why?"],
          "hobbies, tastes, things they recommend, how they talk about them"),
    Topic("opinions", "Opinions",
          "What's something you have strong opinions about?",
          ["What's an unpopular opinion you hold?",
           "What's something you've changed your mind about?"],
          "opinions, hot takes, how they argue a point"),
    Topic("values", "What matters to you",
          "What matters most to you in life right now?",
          ["What's something you won't compromise on?",
           "What are you working towards at the moment?"],
          "values, goals, priorities, boundaries"),
    Topic("style", "How you text",
          "How would you describe the way you text? Anything people tease you about: emoji, slang, punctuation, typos?",
          ["What's a word or phrase you use all the time?",
           "How do you usually react when a friend shares good news?"],
          "texting habits, catchphrases, emoji, humour, how they react"),
    Topic("story", "A recent story",
          "Tell me about something memorable that happened to you recently.",
          ["How did you feel about it?",
           "Who did you tell first, and what did you say?"],
          "a story told in their own voice, emotions, details"),
]

# (id, label, description, follow-ups per topic)
DEPTHS = [
    ("short", "Short", "one question per topic", 0),
    ("standard", "Standard", "with follow-up questions", 2),
    ("deep", "Deep", "more follow-ups, more detail", 4),
]


def depth_followups(depth: str) -> int:
    for d in DEPTHS:
        if d[0] == depth:
            return d[3]
    raise ValueError(f"unknown depth {depth!r}; choose from {[d[0] for d in DEPTHS]}")


def estimate_minutes(topics_left: int, depth: str) -> int:
    questions = topics_left * (1 + depth_followups(depth))
    return max(1, round(questions * SECONDS_PER_QUESTION / 60))


# ── Storage (shares data/elicited.jsonl with reply drills) ────────────────────
def save_turn(topic: Topic, question: str, answer_lines: List[str], path: str = ELICITED_PATH,
              now: Optional[datetime] = None) -> dict:
    record = {
        "kind": "interview",
        "topic": topic.id,
        "question": question,
        "reply": [line for line in answer_lines if line.strip()],
        "answered_at": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
    }
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def load_turns(path: str = ELICITED_PATH) -> List[dict]:
    turns: List[dict] = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict) and record.get("kind") == "interview" and record.get("reply"):
                    turns.append(record)
    except FileNotFoundError:
        pass
    return turns


def topics_done(turns: Sequence[dict]) -> List[str]:
    """Topics with at least one answer, in interview order."""
    answered = {t.get("topic") for t in turns}
    return [t.id for t in TOPICS if t.id in answered]


def transcript(turns: Sequence[dict]) -> "OrderedDict[str, List[dict]]":
    """Turns grouped by topic, in interview order."""
    grouped: "OrderedDict[str, List[dict]]" = OrderedDict((t.id, []) for t in TOPICS)
    for turn in turns:
        if turn.get("topic") in grouped:
            grouped[turn["topic"]].append(turn)
    return OrderedDict((k, v) for k, v in grouped.items() if v)


# ── Persona card ──────────────────────────────────────────────────────────────
def style_section(texts: Sequence[str]) -> List[str]:
    """Texting habits measured from the user's own answers (no LLM needed)."""
    texts = [t for t in texts if t.strip()]
    if len(texts) < 3:
        return []
    p = stylometry.profile(texts)
    lines = [f"Replies are usually around {round(p['words'])} words"
             + (", often split across several messages" if p["messages"] >= 1.5 else "") + "."]
    if p["lower_start"] >= 0.6:
        lines.append("Usually starts messages in lowercase.")
    elif p["lower_start"] <= 0.2:
        lines.append("Usually starts messages with a capital letter.")
    if p["end_period"] <= 0.15:
        lines.append("Rarely ends a message with a full stop.")
    elif p["end_period"] >= 0.6:
        lines.append("Usually ends messages with a full stop.")
    lines.append("Uses emoji often." if p["emoji"] >= 0.5 else
                 "Uses emoji now and then." if p["emoji"] >= 0.15 else "Rarely uses emoji.")
    if p["laugh"] >= 0.25:
        lines.append("Laughs in text a lot (lol / haha).")
    if p["exclaim"] >= 0.4:
        lines.append("Uses exclamation marks freely.")
    if p["ellipsis"] >= 0.25:
        lines.append("Often trails off with '...'.")
    return lines


def _user_texts(turns: Sequence[dict], drills_path: str) -> List[str]:
    texts = ["\n".join(t["reply"]) for t in turns]
    texts += ["\n".join(a["reply"]) for a in load_drill_answers(drills_path)]
    return texts


# Topics whose answers are training data only, not part of the card: the card is
# a profile of who you are, not a record of events (that's retrieval, #16).
_NOT_IN_CARD = {"story"}


def fallback_card(turns: Sequence[dict], drills_path: str = ELICITED_PATH) -> str:
    """A persona card without an LLM: the answers by topic, plus measured style."""
    by_topic = transcript([t for t in turns if t.get("topic") not in _NOT_IN_CARD])
    labels = {t.id: t.label for t in TOPICS}
    out = ["You are role-playing a real person in a text conversation. Reply exactly as "
           "they would, in their voice. Use only the facts below; if asked about something "
           "not covered, answer the way they plausibly would without inventing specifics.",
           "", "## What they've said about themselves", ""]
    for topic_id, items in by_topic.items():
        out.append(f"**{labels[topic_id]}**")
        for item in items:
            answer = " / ".join(item["reply"])
            out.append(f"- Q: {item['question']}\n  A: {answer}")
        out.append("")
    style = style_section(_user_texts(turns, drills_path))
    if style:
        out += ["## How they text", ""] + [f"- {s}" for s in style] + [""]
    return "\n".join(out).rstrip() + "\n"


CARD_PROMPT = """Below is an interview with a real person, plus measured facts about how they text. \
Write a system prompt that will make a language model reply to text messages exactly as this \
person would.

Rules:
- Write in the second person ("You are …").
- Use ONLY facts stated in the interview. Never invent names, places, jobs, events or opinions.
- Sections, in this order, as short bullet lists: Who you are; Work / study; People in your life \
(how you talk to each); Your week and where you live; Interests and tastes; Opinions; What matters \
to you; How you text (combine what they said with the measured habits).
- Keep it a compact profile of who they are (aim for under 300 words). Don't list specific past \
events or anecdotes.
- Skip a section if the interview has nothing for it.
- End with: "If asked about something not covered here, reply the way you plausibly would, \
without inventing specific facts."
- Output only the system prompt, in Markdown.

Interview:
{interview}

Measured texting habits:
{style}"""


def interview_text(turns: Sequence[dict]) -> str:
    labels = {t.id: t.label for t in TOPICS}
    blocks = []
    for topic_id, items in transcript([t for t in turns if t.get("topic") not in _NOT_IN_CARD]).items():
        blocks.append(f"## {labels[topic_id]}")
        for item in items:
            blocks.append(f"Q: {item['question']}\nA: " + "\n   ".join(item["reply"]))
    return "\n".join(blocks)


def llm_card(turns: Sequence[dict], client, model: str, drills_path: str = ELICITED_PATH) -> str:
    style = style_section(_user_texts(turns, drills_path)) or ["(not enough answers to measure)"]
    prompt = CARD_PROMPT.format(interview=interview_text(turns), style="\n".join(f"- {s}" for s in style))
    resp = client.chat.completions.create(model=model, temperature=0.2,
                                          messages=[{"role": "user", "content": prompt}])
    card = (resp.choices[0].message.content or "").strip()
    if card.startswith("```"):
        card = card.strip("`").split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    return card + "\n"


def write_card(card: str, path: str = PERSONA_PATH) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(card)


def load_card(path: str = PERSONA_PATH) -> Optional[str]:
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read().strip()
        return text or None
    except FileNotFoundError:
        return None


# ── Follow-up questions ───────────────────────────────────────────────────────
FOLLOW_UP_PROMPT = """You are a warm, curious interviewer getting to know someone so an AI can \
learn to text like them. Current topic: {label} (dig for: {focus}).

Conversation on this topic so far:
{so_far}

Ask ONE short, casual follow-up question that gets them to share specifics, a story, or how they'd \
actually phrase things. Don't repeat earlier questions. Don't give opinions or advice. If the topic \
is well covered, reply with exactly NEXT. Output only the question (or NEXT)."""


def llm_follow_up(topic: Topic, so_far: Sequence[dict], client, model: str) -> Optional[str]:
    """The next follow-up question, or None when the topic is covered."""
    history = "\n".join(f"Q: {t['question']}\nA: " + " / ".join(t["reply"]) for t in so_far)
    resp = client.chat.completions.create(
        model=model, temperature=0.7,
        messages=[{"role": "user", "content": FOLLOW_UP_PROMPT.format(
            label=topic.label, focus=topic.focus, so_far=history)}])
    question = (resp.choices[0].message.content or "").strip().strip('"')
    if not question or question.upper().startswith("NEXT"):
        return None
    return question.splitlines()[0][:300]
