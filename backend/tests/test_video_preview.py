"""CV preview must remain local, transient, scoped and independent of hosted review."""
import base64
import json
import shutil
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from safety_app.api.video_app import app
from safety_app.domain.people import PersonPreviewRequest
from safety_app.domain.video_analysis import VideoAnalysisRequest
from safety_app.infrastructure.runtime import Runtime
from safety_app.modules.monitoring.recordings import sources


class Detector:
    def __init__(self): self.thresholds = []
    def predict(self, image, confidence=.5):
        self.thresholds.append(confidence)
        return [([10,10,30,60],.8),([40,20,60,60],.2)], {'weights_sha256':'fixture'}


@pytest.fixture
def preview(tmp_path, monkeypatch):
    shutil.copytree(Path(__file__).resolve().parents[2]/'configs',tmp_path/'configs')
    folder=tmp_path/'data/input'; folder.mkdir(parents=True)
    video=folder/'fixture.mp4'
    writer=cv2.VideoWriter(str(video),cv2.VideoWriter_fourcc(*'mp4v'),10,(100,80))
    assert writer.isOpened()
    for i in range(20): writer.write(np.full((80,100,3),i*10,np.uint8))
    writer.release()
    rt=Runtime(tmp_path); rt.people.detector=Detector()
    monkeypatch.setattr('safety_app.infrastructure.runtime._default',rt)
    client=TestClient(app)
    assert client.post('/api/auth/session',json={}).status_code==200
    assert client.post('/api/budgets/stop',json={'stopped':True}).status_code==200
    return rt,client,sources(tmp_path)[0].id,video


def test_preview_with_ai_stopped_streams_and_checks_exact_frame_without_records(preview):
    rt,client,id,video=preview
    before={p.relative_to(rt.root):p.read_bytes() for p in (rt.root/'data').rglob('*') if p.is_file()}
    streamed=client.get(f'/api/preview/{id}/video',headers={'Range':'bytes=0-31'})
    assert streamed.status_code==206 and streamed.content==video.read_bytes()[:32]
    def inspect(position,confidence):
        response=client.post('/api/preview/people',json={'source_id':id,'position_ms':position,'confidence_threshold':confidence})
        assert response.status_code==200,response.text
        return response.json()
    first=inspect(0,.15); later=inspect(1000,.9)
    assert first['status']=='available' and len(first['people'])==2
    assert first['people'][0]['box']==[.1,.125,.2,.625]
    assert later['status']=='available' and later['people']==[]
    assert first['position_ms']==0 and later['position_ms']==1000
    def pixels(row):return cv2.imdecode(np.frombuffer(base64.b64decode(row['image_data_url'].split(',')[1]),np.uint8),cv2.IMREAD_COLOR)
    assert pixels(later).mean()>pixels(first).mean()+80
    assert rt.people.detector.thresholds==[.15,.9]
    assert later['provenance']['confidence_threshold']==.9
    assert {p.relative_to(rt.root):p.read_bytes() for p in (rt.root/'data').rglob('*') if p.is_file()}==before


def test_preview_rejects_unauthenticated_hidden_foreign_and_out_of_range_sources(preview):
    rt,client,id,video=preview
    anon=TestClient(app)
    assert anon.get(f'/api/preview/{id}/video').status_code==401
    assert anon.post('/api/preview/people',json={'source_id':id,'position_ms':0}).status_code==401
    assert client.post('/api/preview/people',json={'source_id':id,'position_ms':0},headers={'Origin':'https://foreign.test'}).status_code==403
    assert client.post('/api/preview/people',json={'source_id':id,'position_ms':2001}).status_code==400
    outside=rt.root/'outside.mp4';shutil.copyfile(video,outside)
    (video.parent/'escape.mp4').symlink_to(outside)
    assert len(sources(rt.root))==1
    assert client.post('/api/preview/people',json={'source_id':'../../outside.mp4','position_ms':0}).status_code==404
    (rt.root/'data/library-hidden.json').write_text(json.dumps({'source_ids':[id]}))
    assert client.get(f'/api/preview/{id}/video').status_code==404
    assert client.post('/api/preview/people',json={'source_id':id,'position_ms':0}).status_code==404
    assert not rt.people.detector.thresholds


def test_busy_preview_and_missing_detector_do_not_look_like_empty_success(preview):
    rt,client,id,_=preview
    rt.preview.lock.acquire()
    try: assert client.post('/api/preview/people',json={'source_id':id,'position_ms':0}).status_code==429
    finally: rt.preview.lock.release()
    class Missing:
        def predict(self,*_): raise FileNotFoundError('Stock weights unavailable')
    rt.people.detector=Missing()
    result=client.post('/api/preview/people',json={'source_id':id,'position_ms':0}).json()
    assert result['status']=='unavailable' and result['detail']=='Stock weights unavailable'
    assert result['image_data_url'].startswith('data:image/jpeg;base64,')
    assert not rt.preview.lock.locked()


@pytest.mark.parametrize('confidence',[0,.049,.951,1,float('nan'),float('inf')])
def test_confidence_validation_is_shared_by_preview_and_saved_runs(confidence):
    with pytest.raises(ValidationError): PersonPreviewRequest(source_id='id',position_ms=0,confidence_threshold=confidence)
    with pytest.raises(ValidationError): VideoAnalysisRequest(source_id='id',question='Inspect safety',idempotency_key='test',person_confidence=confidence)


def test_changed_source_is_rejected_and_preview_lock_released(preview):
    rt,client,id,video=preview
    class Changed(Detector):
        def predict(self,*args):
            with video.open('ab') as stream:stream.write(b'changed fixture')
            return super().predict(*args)
    rt.people.detector=Changed()
    response=client.post('/api/preview/people',json={'source_id':id,'position_ms':0})
    assert response.status_code==400 and 'changed' in response.json()['detail']
    assert not rt.preview.lock.locked()


def test_audio_tail_uses_last_real_video_frame(preview,monkeypatch):
    from types import SimpleNamespace
    from safety_app.modules.monitoring.video import FileReplaySource
    rt,client,id,_=preview
    class WithAudioTail:
        def __init__(self,path):self.decoder=FileReplaySource(path)
        def open(self):
            self.decoder.open()
            self.provenance=self.decoder.provenance;self.stream=self.decoder.stream
            self.container=SimpleNamespace(duration=2300000)
        def seek(self,position,**kwargs):return self.decoder.seek(position,**kwargs)
        def close(self):self.decoder.close()
    monkeypatch.setattr('safety_app.modules.monitoring.preview.FileReplaySource',WithAudioTail)
    response=client.post('/api/preview/people',json={'source_id':id,'position_ms':2300})
    assert response.status_code==200,response.text
    assert response.json()['position_ms']==1900
    assert response.json()['timestamp_ms']==1900
    assert client.post('/api/preview/people',json={'source_id':id,'position_ms':2301}).status_code==400


def test_overlay_payload_uses_displayed_frame_and_omits_image_bytes(preview):
    _,client,id,_=preview
    result=client.post('/api/preview/people',json={'source_id':id,'position_ms':155,'include_image':False}).json()
    assert result['status']=='available'
    assert result['position_ms']==100 and result['timestamp_ms']==100
    assert result['image_data_url']=='' and len(result['people'])==1
    assert result['provenance']['input_width']==100 and result['provenance']['input_height']==80


def test_thumbnails_are_scoped_cached_invalidated_and_never_infer(preview,monkeypatch):
    import os
    from safety_app.modules.monitoring.video import FileReplaySource
    rt,client,id,video=preview
    original=FileReplaySource.open;opened=[]
    def counted(self):opened.append(self.path);return original(self)
    monkeypatch.setattr(FileReplaySource,'open',counted)
    assert TestClient(app).get(f'/api/preview/{id}/thumbnail').status_code==401
    first=client.get(f'/api/preview/{id}/thumbnail')
    assert first.status_code==200 and first.headers['content-type']=='image/jpeg'
    image=cv2.imdecode(np.frombuffer(first.content,np.uint8),cv2.IMREAD_COLOR)
    assert image.shape[:2]==(80,100) and image.mean()>80
    assert client.get(f'/api/preview/{id}/thumbnail').content==first.content
    assert len(opened)==1
    stat=video.stat();os.utime(video,ns=(stat.st_atime_ns,stat.st_mtime_ns+1000000))
    assert client.get(f'/api/preview/{id}/thumbnail').status_code==200
    assert len(opened)==2 and not rt.people.detector.thresholds
    assert not rt.jobs.list() and not rt.db.list('frame')
    (rt.root/'data/library-hidden.json').write_text(json.dumps({'source_ids':[id]}))
    assert client.get(f'/api/preview/{id}/thumbnail').status_code==404
    assert client.get('/api/preview/foreign/thumbnail').status_code==404
