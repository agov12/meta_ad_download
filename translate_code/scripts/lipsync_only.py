"""Run sync.so lip-sync on an existing video + dubbed audio track.

Usage: uv run python scripts/lipsync_only.py <original_video> <dub_audio> <work_dir>

Prints sync.so's cost estimate before submitting, polls to completion, and
writes the lip-synced video into work_dir.
"""

import asyncio
import sys
from pathlib import Path

from ad_localizer import ffmpeg_utils
from ad_localizer.config import load_config, load_settings
from ad_localizer.models import AudioTrack
from ad_localizer.providers.lipsync.syncso import SyncSoLipsyncProvider


async def main() -> None:
    video, audio_path, work_dir = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    settings = load_settings()
    config = load_config()
    assert settings.sync_api_key, "SYNC_API_KEY missing in .env"

    audio = AudioTrack(
        path=audio_path,
        duration_s=ffmpeg_utils.probe_duration(audio_path),
    )
    provider = SyncSoLipsyncProvider(api_key=settings.sync_api_key, model=config.lipsync_model)
    print(f"submitting lipsync: {video.name} + {audio.path.name} (model {config.lipsync_model})", flush=True)
    result = await provider.lipsync(video, audio, work_dir)
    if provider.last_cost_estimate is not None:
        print(f"sync.so estimated cost: ${provider.last_cost_estimate:.2f}", flush=True)
    print(f"Lip-synced video: {result}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
