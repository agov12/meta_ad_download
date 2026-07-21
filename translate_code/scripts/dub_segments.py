"""Segment-aligned dub: Spanish starts/stops with each English sentence.

Flow: extract audio → Demucs (vocals | background) → Whisper transcript
(word timings) → Silero VAD pauses on the vocal stem → punctuation+VAD dub
units → per-unit GPT translation with time budgets → per-unit ElevenLabs
TTS in the cloned voice → each clip time-fitted and placed at its unit's
original start over the background stem → mux onto the untouched video.

No lip-sync, no on-screen text changes.

Usage: uv run python scripts/dub_segments.py <video> [target_lang]
"""

import asyncio
import hashlib
import json
import sys
from dataclasses import asdict
from pathlib import Path

from ad_localizer import ffmpeg_utils, segmentation, separation, timeline
from ad_localizer.config import load_config, load_settings
from ad_localizer.models import AudioTrack, LocalizationJob, Transcript, WordTiming
from ad_localizer.providers.transcription.scribe import ScribeTranscriptionProvider
from ad_localizer.providers.translation.translator import OpenAITranslationProvider
from ad_localizer.providers.voice.elevenlabs import ElevenLabsVoiceProvider


async def get_transcript(job: LocalizationJob, vocal_stem: Path, api_key: str) -> Transcript:
    # Scribe (cached as transcript_scribe.json; whisper's transcript.json is
    # left untouched for the dub_only path)
    cache = job.work_dir / "transcript_scribe.json"
    if cache.exists():
        data = json.loads(cache.read_text())
        data["words"] = [WordTiming(**w) for w in data.get("words", [])]
        print("transcript: cached (scribe)")
        return Transcript(**data)
    transcript = await ScribeTranscriptionProvider(api_key=api_key).transcribe(vocal_stem)
    cache.write_text(json.dumps(asdict(transcript), ensure_ascii=False, indent=2))
    return transcript


def cached_voice_id(job: LocalizationJob) -> str | None:
    meta = job.work_dir / f"dub_{job.target_language}.json"
    if meta.exists():
        return json.loads(meta.read_text()).get("voice_id")
    return None


async def main() -> None:
    video = Path(sys.argv[1]).resolve()
    target = sys.argv[2] if len(sys.argv) > 2 else "es"
    settings = load_settings()
    config = load_config()
    assert settings.openai_api_key, "OPENAI_API_KEY missing in .env"
    assert settings.elevenlabs_api_key, "ELEVENLABS_API_KEY missing in .env"
    ffmpeg_utils.ensure_ffmpeg()

    job = LocalizationJob(
        source_video=video,
        target_language=target,
        work_dir=Path("work") / f"{video.stem}_{target}",
    )
    job.work_dir.mkdir(parents=True, exist_ok=True)
    video_dur = ffmpeg_utils.probe_duration(video)

    # 1. full-quality audio for separation (stereo 44.1k, unlike the 16k
    #    mono Whisper feed - the background stem ends up in the final mix)
    full_audio = job.work_dir / "source_audio.wav"
    if not full_audio.exists():
        ffmpeg_utils._run(
            ["ffmpeg", "-y", "-i", str(video), "-vn", "-ac", "2", "-ar", "44100",
             "-c:a", "pcm_s16le", str(full_audio)]
        )

    # 2. Demucs
    print("separating vocals/background (demucs)...")
    vocals, background = await asyncio.to_thread(
        separation.separate_vocals, full_audio, job.work_dir
    )

    # 3. transcript (Scribe) + 4. pauses from its word gaps + 5. dub units.
    #    Scribe's punctuation is trusted completely: every sentence is one
    #    chunk regardless of length (no sub-splitting, no LLM
    #    re-punctuation); tiny contiguous sentences merge, never across a
    #    pause >= 0.7s (segmentation.BARRIER_PAUSE_S).
    transcript = await get_transcript(job, vocals, settings.elevenlabs_api_key)
    pauses = segmentation.pauses_from_word_gaps(transcript.words)
    translator = OpenAITranslationProvider(api_key=settings.openai_api_key)
    units_cache = job.work_dir / f"units_{target}.json"
    if units_cache.exists():
        units = [
            segmentation.DubUnit(
                u["start"], u["end"], [WordTiming(**w) for w in u["words"]]
            )
            for u in json.loads(units_cache.read_text())
        ]
        print("units: cached")
    else:
        units = segmentation.build_dub_units_from_sentences(transcript.words)
        units_cache.write_text(
            json.dumps(
                [
                    {"start": u.start, "end": u.end, "words": [asdict(w) for w in u.words]}
                    for u in units
                ],
                ensure_ascii=False,
            )
        )
    print(f"\n{len(pauses)} pauses, {len(units)} dub units:")
    for i, u in enumerate(units):
        print(f"  {i}. {u.start:6.2f}-{u.end:6.2f}s ({u.duration:4.1f}s)  {u.text[:70]}")

    # 6. per-unit translation (cached across reruns; delete
    #    segments_<lang>.json to re-translate)
    seg_cache = job.work_dir / f"segments_{target}.json"
    cached_segments = None
    if seg_cache.exists():
        cached_segments = json.loads(seg_cache.read_text())
        if len(cached_segments) != len(units):
            cached_segments = None  # units changed - retranslate
    if cached_segments is not None:
        from ad_localizer.models import TranslatedScript

        translated = TranslatedScript(
            text=" ".join(s["target"] for s in cached_segments),
            source_language=transcript.language,
            target_language=target,
            segments=cached_segments,
        )
        print("translations: cached")
    else:
        audience = {
            "ar": "Arabic speakers (Modern Standard Arabic with a warm, ad-friendly register)",
            "es-MX": "Mexican / Latin American Spanish speakers",
            "es": "Spanish speakers",
            "ja": "Japanese speakers (natural spoken ad register)",
            "ko": "Korean speakers (natural spoken ad register)",
        }.get(target, f"'{target}' speakers")
        translated = await translator.translate_segments(
            transcript, units, target,
            context=f"This is a short social-media video ad. Target audience: {audience}.",
        )
        seg_cache.write_text(json.dumps(translated.segments, ensure_ascii=False, indent=2))
    print()
    for seg in translated.segments:
        print(f"  {seg['index']}. {seg['target'][:70]}")

    # 7. voice: explicit voice id (argv[3]) wins; else reuse the clone from
    #    earlier runs; else clone from the reference clip
    voice = ElevenLabsVoiceProvider(
        api_key=settings.elevenlabs_api_key,
        tts_model=config.elevenlabs_tts_model,
        voice_id=sys.argv[3] if len(sys.argv) > 3 else cached_voice_id(job),
    )
    if voice.voice_id is None:
        # clone from the clean vocal stem, not the mixed audio - background
        # music in the reference audibly degrades the clone
        reference = job.work_dir / f"reference_{video.stem}_clean.mp3"
        if not reference.exists():
            ffmpeg_utils.extract_reference_clip(vocals, reference)
        await voice.ensure_voice(reference)
    print(f"\nvoice: {voice.voice_id}")

    # 8. synthesize each segment and fit it. Policy:
    #    - a segment may end at most OVERRUN_S after its English line (and
    #      never past the next line's start); any amount shorter is fine
    #    - every clip is sped up into [BASE_TEMPO, MAX_TEMPO] (1.04-1.08), so
    #      fastest and slowest segments differ by at most 0.04x
    #    - if a synth misses by <= SAME_TEXT_RETRY_S, retry the SAME phrase
    #      first (ElevenLabs duration varies run to run) before rewording
    OVERRUN_S = 0.4
    SAME_TEXT_RETRY_S = 0.5
    MAX_SAME_TEXT_RETRIES = 2
    MAX_FIT_ATTEMPTS = 5
    seg_dir = job.work_dir / f"segments_{target}"
    placed: list[tuple[Path, float]] = []
    fitted_durations: list[float] = []
    # measured speaking pace (chars/sec), updated from every synthesis; used
    # to reject predictably-too-long rewordings BEFORE paying to synthesize
    pace = {"chars": 0, "seconds": 0.0}

    def pace_cps() -> float:
        return pace["chars"] / pace["seconds"] if pace["seconds"] > 3 else 15.5
    for seg in translated.segments:
        nxt = translated.segments[seg["index"] + 1] if seg["index"] + 1 < len(translated.segments) else None
        # hard cap on the final (post-tempo) duration: the English line's
        # length plus a flat overrun allowance. The tail may overlap the next
        # clip's start by up to OVERRUN_S when speech is continuous (gap 0).
        unit_dur = seg["end"] - seg["start"]
        max_final = unit_dur + OVERRUN_S

        # request stitching context: previous segment ONLY. previous_text
        # keeps tone/prosody continuous across clips; next_text is
        # deliberately NOT sent - it makes the model speak each line as if it
        # runs straight into the following sentence, and the API then chops
        # the audio at the end of the text, leaving hot cut-off line endings
        # (measured: stitched-with-next tails at -17..-25 dB vs -38..-90 dB
        # without).
        prev_text = translated.segments[seg["index"] - 1]["target"] if seg["index"] > 0 else None
        next_text = None

        async def synth_cached(text: str, index: int, take: int = 0):
            # cached by hash of voice + stitching context + text + take number
            # (take > 0 = deliberate re-roll of the same phrase for duration
            # variance); reruns with identical inputs never re-bill
            key = hashlib.sha1(
                f"{voice.voice_id}|{prev_text or ''}|{text}|{take}".encode()
            ).hexdigest()[:10]
            path = seg_dir / f"seg_{index:02d}_{key}.mp3"
            if path.exists():
                return AudioTrack(
                    path=path,
                    duration_s=ffmpeg_utils.probe_duration(path),
                    voice_id=voice.voice_id,
                )
            track = await voice.synthesize_text(
                text, path, previous_text=prev_text, next_text=next_text
            )
            pace["chars"] += len(text)
            pace["seconds"] += track.duration_s
            return track

        # fit loop: accept when the clip fits max_final within the tempo band;
        # near-misses re-roll the same phrase, real misses reword shorter.
        # Keep the BEST attempt (least band violation) if nothing lands.
        text = seg["target"]
        max_raw = max_final * timeline.MAX_TEMPO  # longest acceptable synth

        best_text, best = None, None
        take = 0
        for attempt in range(MAX_FIT_ATTEMPTS):
            raw = await synth_cached(text, seg["index"], take)
            if best is None or max(0.0, raw.duration_s - max_raw) < max(
                0.0, best.duration_s - max_raw
            ):
                best_text, best = text, raw
            if raw.duration_s <= max_raw:
                break  # fits (any amount shorter is fine)
            overshoot = raw.duration_s / timeline.MAX_TEMPO - max_final
            if overshoot <= SAME_TEXT_RETRY_S and take < MAX_SAME_TEXT_RETRIES:
                take += 1
                print(
                    f"  seg {seg['index']}: {raw.duration_s:.1f}s vs {max_raw:.1f}s cap - "
                    f"re-rolling same phrase (take {take + 1})"
                )
                continue
            take = 0
            target_words = max(3, round(len(text.split()) * max_raw / raw.duration_s))
            if target_words >= len(text.split()):
                target_words = len(text.split()) - 1
            if target_words < 3:
                break
            print(
                f"  seg {seg['index']}: {raw.duration_s:.1f}s vs {max_raw:.1f}s cap - "
                f"shortening to ~{target_words} words (attempt {attempt + 1})"
            )
            text = await translator.refit_segment(
                seg["source"], text, target_words, target
            )
            # free pre-filter: if the reworded text's predicted duration is
            # still clearly over the cap, reword again instead of paying to
            # synthesize a known miss
            for _ in range(2):
                predicted = len(text) / pace_cps()
                if predicted <= max_raw * 1.1:
                    break
                target_words = max(3, target_words - 1)
                print(
                    f"  seg {seg['index']}: rewording predicted {predicted:.1f}s > cap - "
                    f"re-shortening to ~{target_words} words (no synth)"
                )
                text = await translator.refit_segment(
                    seg["source"], text, target_words, target
                )
        seg["target"] = best_text

        fitted, tempo = await asyncio.to_thread(
            timeline.fit_clip, best.path, max_final, seg_dir / f"seg_{seg['index']:02d}_fit.wav"
        )
        placed.append((fitted, seg["start"]))
        final_dur = best.duration_s / tempo
        fitted_durations.append(final_dur)
        print(
            f"  seg {seg['index']}: {best.duration_s:.1f}s raw -> {final_dur:.1f}s at "
            f"{tempo:.2f}x (English line {unit_dur:.1f}s, cap {max_final:.1f}s)"
        )

    # total spoken time must not exceed the English original's
    english_total = sum(s["end"] - s["start"] for s in translated.segments)
    spanish_total = sum(fitted_durations)
    verdict = "OK" if spanish_total <= english_total else "OVER - tighten translations"
    print(
        f"\ntotal speech: english {english_total:.1f}s, spanish {spanish_total:.1f}s ({verdict})"
    )

    # persist final (possibly refit) translations
    (job.work_dir / f"segments_{target}.json").write_text(
        json.dumps(translated.segments, ensure_ascii=False, indent=2)
    )

    # 9. assemble over the background stem and mux onto the video
    dub_track = await asyncio.to_thread(
        timeline.assemble, placed, background, job.work_dir / f"dubtrack_{target}.wav"
    )
    final = job.work_dir / f"final_{video.stem}_{target}_segmented.mp4"
    ffmpeg_utils.mux_replace_audio(video, dub_track, final)
    print(f"\nFinal video: {final}")


if __name__ == "__main__":
    asyncio.run(main())
