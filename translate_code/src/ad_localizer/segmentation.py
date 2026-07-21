"""Split a timed transcript into dub units for segment-aligned dubbing.

A dub unit is a stretch of speech whose translation must start and stop at
the same times as the original. Units are defined primarily by sentence
punctuation in the Whisper transcript (each word carries timestamps); the
Silero VAD pause map — computed on the clean Demucs vocal stem — serves two
corrective jobs:

1. Backstop for missing punctuation: casual speech often transcribes as a
   run-on, so any unit longer than MAX_UNIT_S is sub-split at its longest
   internal pauses.
2. Boundary snapping: Whisper word timestamps drift (especially over
   music), so unit edges are snapped to the physically detected speech
   onsets/offsets.

No model makes split decisions here — this is deterministic timestamp
arithmetic over the two inputs.
"""

from dataclasses import dataclass, field
from pathlib import Path

from .models import WordTiming

SENTENCE_END = (".", "!", "?", "…")
MAX_UNIT_S = 12.0      # units longer than this get sub-split at VAD pauses
MIN_UNIT_S = 1.0       # units shorter than this get merged into a neighbor
MIN_PAUSE_S = 0.22     # a VAD gap must be at least this long to count as a pause
SNAP_WINDOW_S = 0.40   # how far a unit edge may move to snap to a VAD edge
MIN_WORD_GAP_S = 0.15  # word-timestamp gap usable as a last-resort split point


@dataclass
class Pause:
    start: float
    end: float

    @property
    def mid(self) -> float:
        return (self.start + self.end) / 2

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class DubUnit:
    start: float
    end: float
    words: list[WordTiming] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(w.word for w in self.words)

    @property
    def duration(self) -> float:
        return self.end - self.start


def detect_pauses(vocal_stem: Path, *, min_pause_s: float = MIN_PAUSE_S) -> list[Pause]:
    """Silence gaps between VAD speech regions on the (clean) vocal stem."""
    from faster_whisper.audio import decode_audio  # lazy: pulls in onnxruntime
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    sampling_rate = 16000
    audio = decode_audio(str(vocal_stem), sampling_rate=sampling_rate)
    # speech_pad_ms widens every speech region on both sides, so it directly
    # shrinks each detected gap by 2x its value - keep it small, then apply
    # our own min_pause_s threshold to the resulting gaps
    regions = get_speech_timestamps(
        audio,
        VadOptions(
            min_silence_duration_ms=100,
            speech_pad_ms=30,
        ),
        sampling_rate=sampling_rate,
    )
    pauses: list[Pause] = []
    prev_end: float | None = None
    for region in regions:
        start = region["start"] / sampling_rate
        end = region["end"] / sampling_rate
        if prev_end is not None and start - prev_end >= min_pause_s:
            pauses.append(Pause(start=prev_end, end=start))
        prev_end = max(prev_end or 0.0, end)
    return pauses


def pauses_from_word_gaps(
    words: list[WordTiming], *, min_pause_s: float = MIN_PAUSE_S
) -> list[Pause]:
    """Pauses read directly from transcript word gaps.

    Only valid for transcribers with acoustic word timestamps (ElevenLabs
    Scribe); Whisper pads words to be contiguous, so its gaps are always ~0
    and this would find nothing - use detect_pauses (VAD) there instead.
    """
    pauses: list[Pause] = []
    for a, b in zip(words, words[1:]):
        if b.start - a.end >= min_pause_s:
            pauses.append(Pause(a.end, b.start))
    return pauses


def build_dub_units_from_sentences(
    words: list[WordTiming], *, min_unit_s: float = 2.0
) -> list[DubUnit]:
    """Sentence-first chunking for fully punctuated transcripts (Scribe).

    The transcript's punctuation is trusted completely: every sentence is
    exactly one chunk, never sub-split regardless of length. Tiny contiguous
    sentences merge into a neighbor, but never across a pause of
    BARRIER_PAUSE_S or more (a real pause is a sync point with the video).
    """
    if not words:
        return []
    units = _split_by_punctuation(words)
    pauses = pauses_from_word_gaps(words)
    return merge_short_units(units, min_unit_s, pauses)


def build_dub_units(
    words: list[WordTiming],
    pauses: list[Pause],
    *,
    max_unit_s: float = MAX_UNIT_S,
    min_unit_s: float = MIN_UNIT_S,
) -> list[DubUnit]:
    """Punctuation-primary units, VAD-backstopped and VAD-snapped."""
    if not words:
        return []

    units = _split_by_punctuation(words)
    units = _subsplit_long_units(units, pauses, max_unit_s)
    units = merge_short_units(units, min_unit_s, pauses)
    _snap_to_vad_edges(units, pauses)
    return units


def _split_by_punctuation(words: list[WordTiming]) -> list[DubUnit]:
    units: list[DubUnit] = []
    current: list[WordTiming] = []
    for word in words:
        current.append(word)
        if word.word.rstrip().endswith(SENTENCE_END):
            units.append(DubUnit(current[0].start, current[-1].end, current))
            current = []
    if current:
        units.append(DubUnit(current[0].start, current[-1].end, current))
    return units


def _subsplit_long_units(
    units: list[DubUnit], pauses: list[Pause], max_unit_s: float
) -> list[DubUnit]:
    result: list[DubUnit] = []
    for unit in units:
        result.extend(_subsplit(unit, pauses, max_unit_s))
    return result


def _subsplit(unit: DubUnit, pauses: list[Pause], max_unit_s: float) -> list[DubUnit]:
    if unit.duration <= max_unit_s or len(unit.words) < 2:
        return [unit]
    # candidate cut points: pauses strictly inside the unit, longest first
    internal = sorted(
        (p for p in pauses if unit.start < p.mid < unit.end),
        key=lambda p: p.duration,
        reverse=True,
    )
    for pause in internal:
        before = [w for w in unit.words if w.end <= pause.mid]
        after = [w for w in unit.words if w.end > pause.mid]
        if not before or not after:
            continue
        left = DubUnit(before[0].start, before[-1].end, before)
        right = DubUnit(after[0].start, after[-1].end, after)
        return _subsplit(left, pauses, max_unit_s) + _subsplit(right, pauses, max_unit_s)
    return _subsplit_at_word_gap(unit, pauses, max_unit_s)


def _subsplit_at_word_gap(
    unit: DubUnit, pauses: list[Pause], max_unit_s: float
) -> list[DubUnit]:
    """Last resort for pause-free stretches: cut at the largest gap between
    consecutive word timestamps (Whisper leaves small gaps at phrase seams
    even when VAD finds no clean silence)."""
    gaps = [
        (unit.words[i + 1].start - unit.words[i].end, i)
        for i in range(len(unit.words) - 1)
    ]
    gap, i = max(gaps, default=(0.0, -1))
    if gap < MIN_WORD_GAP_S:
        return [unit]  # genuinely seamless - keep whole rather than cut mid-word
    before, after = unit.words[: i + 1], unit.words[i + 1 :]
    left = DubUnit(before[0].start, before[-1].end, before)
    right = DubUnit(after[0].start, after[-1].end, after)
    return _subsplit(left, pauses, max_unit_s) + _subsplit(right, pauses, max_unit_s)


def split_unit_by_sentences(unit: DubUnit, sentences: list[str]) -> list[DubUnit]:
    """Split a pause-free unit at sentence boundaries mapped to word timings.

    ``sentences`` is the unit's text re-punctuated into sentences (words
    verbatim, e.g. by an LLM). Words are consumed sequentially by count, so
    each sub-unit's start/end come from its own words' timestamps. Falls
    back to the whole unit if the sentence words don't add up.
    """
    counts = [len(s.split()) for s in sentences if s.split()]
    if len(counts) < 2 or sum(counts) != len(unit.words):
        return [unit]
    result: list[DubUnit] = []
    cursor = 0
    for count in counts:
        chunk = unit.words[cursor : cursor + count]
        result.append(DubUnit(chunk[0].start, chunk[-1].end, chunk))
        cursor += count
    return result


# a real pause this long in the original video is a sync point the dub must
# preserve: units on either side stay separate chunks, never merged across
BARRIER_PAUSE_S = 0.7


def merge_short_units(
    units: list[DubUnit],
    min_unit_s: float,
    pauses: list[Pause] | None = None,
    *,
    barrier_pause_s: float = BARRIER_PAUSE_S,
) -> list[DubUnit]:
    """Merge sub-min_unit_s units into a neighbor - but never across a pause
    of barrier_pause_s or more, so the chunk map stays consistent with the
    original video's rhythm (the speaker is silent there; the dub should be
    too)."""
    if not units:
        return units

    def barrier_between(a: DubUnit, b: DubUnit) -> bool:
        for p in pauses or []:
            if p.duration >= barrier_pause_s and a.end - 0.01 <= p.mid <= b.start + 0.01:
                return True
        return False

    merged: list[DubUnit] = [units[0]]
    for unit in units[1:]:
        short = merged[-1].duration < min_unit_s or unit.duration < min_unit_s
        if short and not barrier_between(merged[-1], unit):
            prev = merged[-1]
            merged[-1] = DubUnit(prev.start, unit.end, prev.words + unit.words)
        else:
            merged.append(unit)
    return merged


def _snap_to_vad_edges(units: list[DubUnit], pauses: list[Pause]) -> None:
    """Align unit edges to physical speech boundaries where they're close.

    A unit that starts just after a pause snaps its start to the pause's
    end (the true speech onset); a unit that ends just before a pause snaps
    its end to the pause's start (the true speech offset).
    """
    for unit in units:
        for pause in pauses:
            if abs(unit.start - pause.end) <= SNAP_WINDOW_S and pause.end < unit.end:
                unit.start = pause.end
            if abs(unit.end - pause.start) <= SNAP_WINDOW_S and pause.start > unit.start:
                unit.end = pause.start
