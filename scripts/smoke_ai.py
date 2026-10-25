"""Explicit billable multimodal integration smoke; ordinary tests never execute this."""
import argparse
import asyncio
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
import cv2
from dotenv import load_dotenv
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend/src'))
from app.agents.service import ReviewService
from app.evidence.store import EvidenceStore
from app.models.contracts import FramePacket, LabConfig, RuleCandidate
from app.storage.repository import Repository

async def main(args):
    load_dotenv()
    image=cv2.imread(str(args.image))
    if image is None: raise SystemExit('Cannot decode image')
    root=Path('data/artifacts/smoke-ai'); root.mkdir(parents=True,exist_ok=True)
    store=EvidenceStore(root/'evidence'); repo=Repository(root/'runs.sqlite')
    context={'source_id':'explicit-smoke-image','camera_id':'smoke-camera','session_id':'smoke-'+args.image.stem,'run_generation':0}
    height,width=image.shape[:2]
    store.add(FramePacket(**context,frame_id=1,timestamp_ms=0,width=width,height=height,evidence_cutoff_ms=0),image)
    candidate=RuleCandidate(**context,id='smoke-scene',kind='scene_review',timestamp_ms=0,start_ms=0,evidence_cutoff_ms=0,limitations=['Single-image smoke test; no validated hazard detection'])
    repo.save('candidate',candidate)
    service=ReviewService(store,repo,lambda:LabConfig(),lambda s,g:True)
    service.set_enabled(True,True)
    await service.manual_review(candidate)
    await service.queue.join()
    runs=repo.list('agent_run'); print(runs[-1])
    output=repo.list('assessment')
    if output: print(output[-1])
    await service.close()
    if not runs or runs[-1]['status']!='reviewed': raise SystemExit(1)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description='Send actual local JPEG bytes to configured Luna via Pydantic AI Responses. Billable.')
    parser.add_argument('--image',type=Path,required=True)
    parser.add_argument('--allow-billable',action='store_true',required=True)
    parser.add_argument('--allow-hosted-image',action='store_true',required=True)
    args=parser.parse_args()
    asyncio.run(main(args))
