from fractions import Fraction
from types import SimpleNamespace
import pytest
from app.video.sources import FileReplaySource, RTSPSource


def test_decode_uses_pts_time_base_and_preserves_irregular_spacing():
    source = FileReplaySource("unused")
    source.decoder = iter(SimpleNamespace(pts=p, time_base=Fraction(1,90000), to_ndarray=lambda format: format)
                          for p in [9000, 12330, 99000])
    decoded = [source.read() for _ in range(3)]
    assert [f.timestamp_ms for f in decoded] == [100,137,1100]
    assert [f.frame_id for f in decoded] == [100000,137000,1100000]
    assert all(f.image == "bgr24" for f in decoded)


def test_missing_pts_fails_explicitly_and_live_source_cannot_seek():
    source = FileReplaySource("unused")
    source.decoder = iter([SimpleNamespace(pts=None, time_base=Fraction(1,90000))])
    with pytest.raises(ValueError, match="presentation timestamp"):
        source.read()
    with pytest.raises(ValueError, match="seek"):
        RTSPSource("rtsp://unused").seek(100)

@pytest.mark.asyncio
async def test_exhausted_seek_returns_explicit_error_across_thread_boundary():
    import asyncio
    source=FileReplaySource('unused')
    source.stream=SimpleNamespace(time_base=Fraction(1,1000))
    source.container=SimpleNamespace(seek=lambda *a,**kw:None,decode=lambda stream:iter([]))
    with pytest.raises(ValueError,match='beyond available'):
        await asyncio.wait_for(asyncio.to_thread(source.seek,999999),1)

def test_seek_to_stream_end_returns_final_frame_but_beyond_duration_is_rejected():
    source=FileReplaySource('unused')
    source.stream=SimpleNamespace(time_base=Fraction(1,1000),duration=1000,start_time=0)
    frame=SimpleNamespace(pts=900,time_base=Fraction(1,1000),to_ndarray=lambda format:format)
    source.container=SimpleNamespace(seek=lambda *a,**kw:None,decode=lambda stream:iter([frame]))
    assert source.seek(1000).timestamp_ms==900
    with pytest.raises(ValueError,match='beyond available'):source.seek(1001)
