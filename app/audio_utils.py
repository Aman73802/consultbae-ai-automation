"""Audio property extraction for Task 3.

Built on pydub (which shells out to ffmpeg/ffprobe) rather than librosa:
everything required here -- duration, sample rate, loudness, a rough
noise estimate -- is a couple of pydub/ffprobe calls away, without
pulling in librosa's numpy/numba/llvmlite build chain for a take-home.

ffmpeg/ffprobe are provided by the `static-ffmpeg` pip package (a
self-contained static binary) instead of assuming they're installed on
the host system -- this machine's Homebrew was in a broken/unwritable
state, and requiring a working system ffmpeg install is one more thing
that can go wrong when someone else clones this repo to run it.
"""
import static_ffmpeg

static_ffmpeg.add_paths()

from pydub import AudioSegment
from pydub.utils import mediainfo

SILENCE_CHUNK_MS = 200
SILENCE_THRESHOLD_DB = -40.0


def extract_properties(file_path):
    seg = AudioSegment.from_file(file_path)

    duration_sec = round(len(seg) / 1000.0, 3)
    sample_rate_khz = round(seg.frame_rate / 1000.0, 3)
    loudness_db = None if seg.dBFS == float("-inf") else round(seg.dBFS, 2)
    bitrate_kbps = _bitrate_kbps(file_path, seg)
    silence_ratio = _silence_ratio(seg)
    quality_estimate = _quality_estimate(loudness_db, silence_ratio)

    return {
        "duration_sec": duration_sec,
        "sample_rate_khz": sample_rate_khz,
        "bitrate_kbps": bitrate_kbps,
        "loudness_db": loudness_db,
        "silence_ratio": silence_ratio,
        "quality_estimate": quality_estimate,
    }


def _bitrate_kbps(file_path, seg):
    """Prefer the container's actual reported bitrate (via ffprobe); fall
    back to the raw PCM-equivalent bitrate for formats that don't report
    one (e.g. an uncompressed WAV recorded straight from the browser)."""
    try:
        info = mediainfo(file_path)
        br = info.get("bit_rate")
        if br:
            return round(int(br) / 1000.0, 1)
    except Exception:
        pass
    return round(seg.frame_rate * seg.sample_width * 8 * seg.channels / 1000.0, 1)


def _silence_ratio(seg, chunk_ms=SILENCE_CHUNK_MS, threshold_db=SILENCE_THRESHOLD_DB):
    """Bonus: fraction of fixed-size chunks whose loudness falls below a
    silence threshold -- a rough, dependency-free stand-in for a real
    VAD/noise model."""
    n_chunks = max(1, len(seg) // chunk_ms)
    silent = 0
    for i in range(n_chunks):
        chunk = seg[i * chunk_ms:(i + 1) * chunk_ms]
        if chunk.dBFS == float("-inf") or chunk.dBFS < threshold_db:
            silent += 1
    return round(silent / n_chunks, 3)


def _quality_estimate(loudness_db, silence_ratio):
    if loudness_db is None:
        return "silent / unreadable"
    if silence_ratio > 0.6:
        return "mostly silence"
    if loudness_db < -35:
        return "low volume / possibly poor quality"
    if loudness_db > -3:
        return "possibly clipping / too loud"
    return "ok"
