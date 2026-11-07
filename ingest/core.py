"""Platform-agnostic pipeline core.

Operates purely on :class:`~ingest.message.NormalizedMessage` streams, so it is
identical for every source. Two tunables:

* ``conversation_gap`` — seconds of silence that start a new conversation.
* ``message_chain`` — max seconds between same-sender messages for them to be
  merged into a single turn.

A *sample* is a list of ``{"role": "user"|"assistant", "text": ...}`` turns.
"""

from typing import Dict, Iterable, List, Optional

from ingest.message import NormalizedMessage

DEFAULT_CONVERSATION_GAP = 3600  # 1 hour of silence starts a new conversation
DEFAULT_MESSAGE_CHAIN = 30       # consecutive same-sender messages within 30s chain into one turn

# How group chats (more than two people) are turned into samples.
GROUP_CHAT_MODES = ("keep", "focus", "skip")
DEFAULT_GROUP_CHATS = "keep"

Turn = Dict[str, str]
Sample = List[Turn]


def _group_by_chat(messages: Iterable[NormalizedMessage]) -> List[List[NormalizedMessage]]:
    """Group messages by ``chat_id``, each sorted chronologically.

    Conversations never span chats, so each chat is processed independently.
    Messages are sorted by timestamp (stable, so same-timestamp order is
    preserved) because not every source guarantees chronological order.
    """
    groups: "Dict[str, List[NormalizedMessage]]" = {}
    for msg in messages:
        groups.setdefault(msg.chat_id, []).append(msg)
    return [sorted(g, key=lambda m: m.timestamp) for g in groups.values()]


def _split_into_conversations(
    messages: List[NormalizedMessage], gap_threshold: int
) -> List[List[NormalizedMessage]]:
    """Split one chat's messages into conversations on silence gaps."""
    conversations: List[List[NormalizedMessage]] = []
    current: List[NormalizedMessage] = []
    last_ts = None

    for msg in messages:
        if last_ts is not None and (msg.timestamp - last_ts) > gap_threshold:
            if current:
                conversations.append(current)
            current = []
        current.append(msg)
        last_ts = msg.timestamp

    if current:
        conversations.append(current)

    return conversations


def _merge_by_reply(
    conversations: List[List[NormalizedMessage]],
) -> List[List[NormalizedMessage]]:
    """Stitch back conversations that a silence gap split but a reply connects.

    A time gap is a guess at where one conversation ends. An explicit reply link
    is ground truth: if a message replies to one in an earlier (same-chat)
    conversation, they belong together. We union such conversations and re-sort
    each merged group chronologically.

    When no message carries reply metadata (``message_id``/``reply_to_id`` all
    ``None``), there is nothing to union and the input is returned unchanged — so
    sources without reply data keep the pure time-based behaviour.
    """
    n = len(conversations)
    if n <= 1:
        return conversations

    id_to_conv = {
        m.message_id: ci
        for ci, conv in enumerate(conversations)
        for m in conv
        if m.message_id is not None
    }
    if not id_to_conv:
        return conversations

    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    for ci, conv in enumerate(conversations):
        for m in conv:
            target = id_to_conv.get(m.reply_to_id) if m.reply_to_id else None
            if target is not None and target != ci:
                union(ci, target)

    groups: "Dict[int, List[NormalizedMessage]]" = {}
    for ci in range(n):
        groups.setdefault(find(ci), []).extend(conversations[ci])

    # Order merged groups by their earliest message so output stays chronological.
    ordered_roots = sorted(groups, key=lambda r: min(m.timestamp for m in groups[r]))
    return [sorted(groups[r], key=lambda m: m.timestamp) for r in ordered_roots]


def _is_group_chat(chat_messages: List[NormalizedMessage]) -> bool:
    """Whether a chat really has more than one other person in it.

    Counting distinct sender names isn't enough: in a 1:1 chat the other
    person may change their display name ("Bob", later "Bob Tan"), which would
    look like two people. So a chat counts as a group when it has three or more
    other senders, or exactly two whose activity *overlaps* in time (one speaks,
    then the other, then the first again), which a rename never does.
    """
    spans: Dict[str, List[int]] = {}
    for m in chat_messages:
        if m.sender_is_self:
            continue
        span = spans.setdefault(m.sender_id, [m.timestamp, m.timestamp])
        span[0] = min(span[0], m.timestamp)
        span[1] = max(span[1], m.timestamp)
    if len(spans) < 2:
        return False
    if len(spans) >= 3:
        return True
    ordered = sorted(spans.values())
    latest_end = ordered[0][1]
    for start, end in ordered[1:]:
        if start <= latest_end:
            return True
        latest_end = max(latest_end, end)
    return False


def _direct_exchanges(
    conversation: List[NormalizedMessage],
    by_id: Dict[str, NormalizedMessage],
) -> List[List[NormalizedMessage]]:
    """Pick out the parts of a group conversation that read like a 1:1.

    In a busy group most of your messages aren't replies to whatever came just
    before them, so pairing by time order alone produces non-sequiturs. This
    keeps:

    * **Explicit replies** (sources with reply links, e.g. Telegram or Signal):
      a message of yours that replies to someone is paired with the message it
      replies to, along with what you sent straight after it, even if others
      spoke in between. These are taken first, since the reply link says who
      you were answering.
    * **Direct exchanges:** among the remaining messages, stretches where only
      you and *one* other person are talking. Another person speaking, or one of
      your explicit replies, ends the stretch. Stretches without both you and
      the other person are dropped.

    ``by_id`` maps message ids to messages across the whole chat (a reply can
    point into an earlier conversation).
    """
    exchanges: List[List[NormalizedMessage]] = []
    claimed = set()  # ids of messages used in an explicit-reply pair
    for i, msg in enumerate(conversation):
        if id(msg) in claimed or not (msg.sender_is_self and msg.reply_to_id):
            continue
        target = by_id.get(msg.reply_to_id)
        if target is None or target.sender_is_self:
            continue
        # Your reply plus any messages you sent straight after it (the turn),
        # stopping at your next explicit reply, which gets its own pair.
        turn = [msg]
        for nxt in conversation[i + 1:]:
            if not nxt.sender_is_self or nxt.reply_to_id:
                break
            turn.append(nxt)
        claimed.update(id(m) for m in turn)
        exchanges.append([target] + turn)

    current: List[NormalizedMessage] = []
    other: Optional[str] = None

    def close() -> None:
        if any(m.sender_is_self for m in current) and any(not m.sender_is_self for m in current):
            exchanges.append(list(current))

    for msg in conversation:
        if id(msg) in claimed:
            close()
            current, other = [], None
            continue
        if not msg.sender_is_self:
            if other is not None and msg.sender_id != other:
                close()
                current = []
            other = msg.sender_id
        current.append(msg)
    close()

    # Chronological order by each sample's first message.
    return sorted(exchanges, key=lambda ex: ex[0].timestamp)


def _collect_turn(
    conversation: List[NormalizedMessage], start_idx: int, chain_threshold: int
):
    """Collect consecutive messages from one sender starting at ``start_idx``.

    Chains them while each is within ``chain_threshold`` seconds of the previous
    one in the chain. Returns ``(texts, next_idx)``.
    """
    sender = conversation[start_idx].sender_id
    texts: List[str] = []
    last_ts = None
    j = start_idx

    while j < len(conversation):
        msg = conversation[j]
        if msg.sender_id != sender:
            break
        if last_ts is not None and (msg.timestamp - last_ts) > chain_threshold:
            break
        texts.append(msg.text)
        last_ts = msg.timestamp
        j += 1

    return texts, j


def _assemble_turns(raw_turns, multi_speaker: bool) -> Sample:
    """Turn ``(sender_id, is_self, text)`` runs into role/text turns.

    Roles: the dataset owner is ``assistant`` (this is what the doppelganger
    learns to produce, so it is *never* labelled), everyone else is ``user``.

    Default mode merges adjacent same-role runs, so in a group chat several
    people on the "other side" collapse into one ``user`` turn. ``multi_speaker``
    instead keeps each speaker distinct and prefixes ``user`` turns with the
    sender (``"Bob: ..."``), only merging consecutive runs from the *same*
    sender — preserving who-said-what as conditioning context.
    """
    turns: Sample = []
    last_sender = None

    for sender_id, is_self, text in raw_turns:
        role = "assistant" if is_self else "user"

        same_role = bool(turns) and turns[-1]["role"] == role
        # In multi-speaker mode a user turn only merges with the previous turn
        # when it is the same speaker; otherwise distinct speakers stay distinct.
        mergeable = same_role and not (
            multi_speaker and role == "user" and last_sender != sender_id
        )
        if mergeable:
            # Continuation of the same turn — don't repeat the speaker prefix.
            turns[-1]["text"] += "\n" + text
        else:
            value = f"{sender_id}: {text}" if (multi_speaker and role == "user") else text
            turns.append({"role": role, "text": value})
        last_sender = sender_id

    return turns


def _to_sample(
    conversation: List[NormalizedMessage], message_chain: int, multi_speaker: bool
) -> Optional[Sample]:
    """Merge one conversation into turns; None unless it has both roles."""
    raw_turns = []
    i = 0
    while i < len(conversation):
        texts, next_i = _collect_turn(conversation, i, message_chain)
        if texts:
            m = conversation[i]
            raw_turns.append((m.sender_id, m.sender_is_self, "\n".join(texts)))
        i = next_i

    turns = _assemble_turns(raw_turns, multi_speaker)
    roles = {t["role"] for t in turns}
    return turns if "user" in roles and "assistant" in roles else None


def build_samples(
    messages: Iterable[NormalizedMessage],
    conversation_gap: int = DEFAULT_CONVERSATION_GAP,
    message_chain: int = DEFAULT_MESSAGE_CHAIN,
    multi_speaker: bool = False,
    group_chats: str = DEFAULT_GROUP_CHATS,
    stats: Optional[Dict[str, int]] = None,
) -> List[Sample]:
    """Turn normalized messages into multi-turn conversation samples.

    Splits each chat into conversations (stitching reply-linked ones back
    together), merges consecutive same-sender messages into turns, and keeps
    only conversations containing at least one user turn and one assistant turn.

    ``multi_speaker`` preserves and labels individual senders in group chats
    (see :func:`_assemble_turns`); the default collapses the other side.

    ``group_chats`` controls chats with more than two people: ``"keep"`` treats
    them like 1:1 chats, ``"focus"`` keeps only the parts that read like a 1:1
    (see :func:`_direct_exchanges`), ``"skip"`` leaves them out. If ``stats`` is
    given, it is filled with ``chats``, ``group_chats``, ``samples`` and
    ``group_samples`` counts.
    """
    if group_chats not in GROUP_CHAT_MODES:
        raise ValueError(f"group_chats must be one of {GROUP_CHAT_MODES}, got {group_chats!r}")
    samples: List[Sample] = []
    counts = {"chats": 0, "group_chats": 0, "samples": 0, "group_samples": 0}

    for chat_messages in _group_by_chat(messages):
        is_group = _is_group_chat(chat_messages)
        counts["chats"] += 1
        counts["group_chats"] += is_group
        if is_group and group_chats == "skip":
            continue
        time_convs = _split_into_conversations(chat_messages, conversation_gap)
        by_id = (
            {m.message_id: m for m in chat_messages if m.message_id is not None}
            if is_group and group_chats == "focus" else {}
        )
        for conversation in _merge_by_reply(time_convs):
            parts = (
                _direct_exchanges(conversation, by_id)
                if is_group and group_chats == "focus"
                else [conversation]
            )
            for part in parts:
                sample = _to_sample(part, message_chain, multi_speaker)
                if sample is not None:
                    samples.append(sample)
                    counts["group_samples"] += is_group

    counts["samples"] = len(samples)
    if stats is not None:
        stats.update(counts)
    return samples
