"""Load one or more chat exports into a single set of conversation samples.

Most people's texting is spread over several apps, so ingest can take several
exports in one run and write one combined dataset::

    python -m ingest --input telegram:data/result.json \\
                     --input whatsapp:"data/WhatsApp Chat with Alice.txt" \\
                     --input signal:data/signal-export-2026-11-07/ \\
                     --self-name "whatsapp:Yu Sheng"

An ``--input`` written as ``<source>:<path>`` names its own source. A plain path
uses ``--source`` (the single-export form, unchanged). ``--self-name`` works the
same way: ``<source>:<name>`` applies to that source only, and a plain name
applies to every source that doesn't have its own. Your name usually differs
between apps, so per-source names are the normal case.

Each export is parsed and turned into samples on its own. Conversations never
span chats, and chats never span exports, so this gives the same samples as one
big pass. It also makes it easy to report what each export contributed. When
more than one export is given, chat ids are prefixed with the export's position
and source (``2:whatsapp:WhatsApp Chat with Alice``), so two chats from
different exports can never be merged into one conversation, even if their ids
happen to match.
"""

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple, Union

from ingest import core
from ingest.adapters import available_sources, get_adapter


DEFAULT_INPUT = os.path.join("data", "result.json")


class SourceLoadError(Exception):
    """An export couldn't be loaded. The message names the source and path; the
    original error is chained as ``__cause__``."""


@dataclass
class SourceSpec:
    """One export to load: which adapter, which path, and who "you" are in it.

    ``self_names`` holds every name given for this source. With two exports from
    the same app you may appear under different names, so each export uses the
    first name that actually matches one of its senders.
    """

    source: str
    path: str
    self_names: List[str] = field(default_factory=list)

    @property
    def self_name(self) -> Optional[str]:
        return self.self_names[0] if self.self_names else None


@dataclass
class SourceResult:
    spec: SourceSpec
    messages: int
    samples: int


def _split_prefix(value: str) -> Tuple[Optional[str], str]:
    """Split ``"<source>:<rest>"`` when ``<source>`` is a known adapter.

    Anything else (a plain path, a Windows path like ``C:\\...``, a name that
    happens to contain a colon) is returned unchanged with no source.
    """
    head, sep, rest = value.partition(":")
    if sep and head.strip().lower() in available_sources():
        return head.strip().lower(), rest
    return None, value


def _as_list(value: Union[str, Sequence[str], None]) -> List[str]:
    if value is None:
        return []
    return [value] if isinstance(value, str) else list(value)


def resolve_specs(
    inputs: Union[str, Sequence[str], None],
    default_source: str,
    self_names: Union[str, Sequence[str], None] = (),
) -> List[SourceSpec]:
    """Turn ``--input`` / ``--self-name`` values into :class:`SourceSpec` s.

    Each argument may be a single string, a list, or None. No inputs means
    :data:`DEFAULT_INPUT`. Raises ValueError for an unknown ``--source``, or for
    a source-specific ``--self-name`` whose source isn't among the inputs
    (almost always a typo).
    """
    inputs = _as_list(inputs) or [DEFAULT_INPUT]
    self_names = _as_list(self_names)
    if default_source not in available_sources():
        raise ValueError(
            f"Source {default_source!r} is not supported. Supported sources: "
            f"{', '.join(available_sources())}."
        )

    shared: List[str] = []
    per_source: Dict[str, List[str]] = {}
    for value in self_names:
        source, name = _split_prefix(value)
        if source:
            per_source.setdefault(source, []).append(name)
        else:
            shared.append(value)

    specs: List[SourceSpec] = []
    seen = set()
    for value in inputs:
        source, path = _split_prefix(value)
        source = source or default_source
        key = (source, os.path.abspath(path))
        if key in seen:
            print(f"[sources] Skipping duplicate input {value!r}.")
            continue
        seen.add(key)
        specs.append(SourceSpec(source, path, list(per_source.get(source, shared))))

    unused = sorted(set(per_source) - {s.source for s in specs})
    if unused:
        raise ValueError(
            f"--self-name given for {', '.join(unused)}, but no --input uses "
            f"{'that source' if len(unused) == 1 else 'those sources'}."
        )
    return specs


def load_samples(
    specs: Sequence[SourceSpec],
    conversation_gap: int = core.DEFAULT_CONVERSATION_GAP,
    message_chain: int = core.DEFAULT_MESSAGE_CHAIN,
    multi_speaker: bool = False,
) -> Tuple[List[core.Sample], List[SourceResult]]:
    """Parse each export, build its samples, and combine them.

    Returns ``(samples, per-source results)``. An adapter error (missing file,
    unrecognised format, can't tell who "you" are, bad JSON or encoding) is
    raised as :class:`SourceLoadError` naming the failing source and path.
    """
    namespaced = len(specs) > 1
    samples: List[core.Sample] = []
    results: List[SourceResult] = []
    for i, spec in enumerate(specs, 1):
        label = f"'{spec.source}'" + (f" ({i}/{len(specs)})" if namespaced else "")
        print(f"Loading {spec.path} via {label} adapter...")
        try:
            messages = _parse_with_names(spec)
        except Exception as e:
            raise SourceLoadError(f"[{spec.source}: {spec.path}] {e}") from e
        if namespaced:
            for m in messages:
                m.chat_id = f"{i}:{spec.source}:{m.chat_id}"
        built = core.build_samples(
            messages,
            conversation_gap=conversation_gap,
            message_chain=message_chain,
            multi_speaker=multi_speaker,
        )
        samples.extend(built)
        results.append(SourceResult(spec, len(messages), len(built)))
    return samples, results


def _parse_with_names(spec: SourceSpec):
    """Parse one export, trying each candidate self name in turn.

    The first name under which some message is recognised as yours wins. If none
    is, the first name's result is kept (the adapter has already warned).
    """
    adapter = get_adapter(spec.source)
    candidates = spec.self_names or [None]
    first = None
    for name in candidates:
        messages = adapter.parse(spec.path, self_name=name)
        if first is None:
            first = messages
        if len(candidates) == 1 or any(m.sender_is_self for m in messages):
            return messages
    return first


def print_summary(results: Sequence[SourceResult]) -> None:
    """Show what each export contributed (only worth printing for 2+)."""
    if len(results) < 2:
        return
    width = max(len(r.spec.source) for r in results)
    print("Per-source summary:")
    for r in results:
        print(f"  {r.spec.source:<{width}}  {r.messages:>7} messages  "
              f"{r.samples:>6} samples  {r.spec.path}")
    if any(r.samples == 0 for r in results):
        print("  Warning: an export contributed no samples. Check its --self-name "
              "(your messages must be recognised) and that the file is the right export.")
