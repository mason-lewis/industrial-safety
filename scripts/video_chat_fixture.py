#!/usr/bin/env python3
"""Real local decoding with mocked agents. Never a hosted accuracy evaluation."""
import asyncio,json,os,shutil,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'backend'),str(ROOT/'backend/src')]
os.environ.update(SAFETY_PAID_REVIEW='0',AI_ALWAYS_ON='false',AI_ENABLED='false',HOSTED_IMAGES_ALLOWED='false',OPENAI_API_KEY='')
from pydantic_ai import models
models.ALLOW_MODEL_REQUESTS=False

async def main():
    import av,cv2,numpy as np
    from fastapi.testclient import TestClient
    from safety_app.infrastructure.runtime import Runtime
    from safety_app.domain.video_analysis import VideoAnalysisRequest
    from safety_app.domain.video_chat import VideoChatInput,VideoChatFocus
    from safety_app.modules.monitoring.recordings import sources
    from safety_app.modules.safety_review.video_analysis import VideoAnalysisService
    from safety_app.modules.safety_review.video_chat import VideoChat
    from safety_app.workers.runner import execute
    from safety_app.api.auth import Operator
    root=Path(tempfile.mkdtemp(prefix='video-chat-fixture-'))
    shutil.copytree(ROOT/'configs',root/'configs');shutil.copy(ROOT/'README.md',root/'README.md')
    (root/'data/input').mkdir(parents=True)
    with av.open(str(root/'data/input/fixture-moments.mp4'),'w') as output:
        stream=output.add_stream('libx264',rate=10);stream.width=640;stream.height=360;stream.pix_fmt='yuv420p'
        for i in range(110):
            pixels=np.full((360,640,3),(35,48,65) if i<50 else (60,72,42),np.uint8)
            cv2.rectangle(pixels,(80+i*2,160),(140+i*2,240),(180,200,220),-1)
            cv2.putText(pixels,'FIXTURE FOOTAGE',(32,60),cv2.FONT_HERSHEY_SIMPLEX,.7,(235,240,245),2)
            cv2.putText(pixels,f'{i/10:.1f} seconds',(32,310),cv2.FONT_HERSHEY_SIMPLEX,.6,(200,210,220),1)
            if 33<=i<=35:cv2.circle(pixels,(420,160),18,(60,80,230),-1)
            for packet in stream.encode(av.VideoFrame.from_ndarray(pixels,format='bgr24')):output.mux(packet)
        for packet in stream.encode():output.mux(packet)
    rt=Runtime(root)
    review=VideoAnalysisService(rt).request(VideoAnalysisRequest(source_id=sources(root)[0].id,question='Demonstrate visual review',review_strategy='full',mode='fixture',idempotency_key='review'),'local')
    result=await execute(rt,review);assert result.status=='completed',result.error
    for child in list(rt.jobs.list()):
        if child.kind=='video_safety_review':assert (await execute(rt,child)).status=='completed'
    chat=VideoChat(rt);question=chat.submit(review.run_id,VideoChatInput(message='What is visible here?',mode='fixture',idempotency_key='question',focus=VideoChatFocus(timestamp_ms=3300)),Operator())
    job=rt.jobs.claim(['video_chat']);assert (await execute(rt,job)).status=='completed'
    saved=chat.get(review.run_id,question.id,'local');assert saved.answer.claims and saved.inspections
    import safety_app.infrastructure.runtime as runtime_module
    runtime_module._default=rt
    from safety_app.api.video_app import app
    client=TestClient(app);assert client.post('/api/auth/session',json={}).status_code==200
    base=f'/api/review-runs/{review.run_id}/chat'
    exported=client.get(base+'/export');assert exported.status_code==200
    events=client.get(base+f'/turns/{question.id}/events');assert 'event: settled' in events.text
    for frame in saved.evidence:assert client.get(base+f'/turns/{question.id}/frames/{frame.id}').status_code==200
    with rt.db.connect() as db:paid=db.execute('SELECT count(*) FROM reservations').fetchone()[0]
    assert paid==0
    receipt={'root':str(root),'review_run_id':review.run_id,'turn_id':question.id,'outcome':saved.answer.outcome,'inspections':len(saved.inspections),'retained_frames':len(saved.evidence),'provider_requests':paid,'stream_reconnect_and_export':'passed','quality':'Synthetic judgments; not human ground truth.'}
    (root/'chat-export.json').write_text(exported.text);(root/'receipt.json').write_text(json.dumps(receipt,indent=2))
    print(json.dumps(receipt,indent=2))
if __name__=='__main__':asyncio.run(main())
