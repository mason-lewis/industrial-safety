"""Bounded local CV/FFmpeg replay. No language-model client, training, or weight download."""
import argparse,asyncio,json,os,tempfile,time
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'backend'),str(ROOT/'backend/src')]
os.environ['HF_HUB_OFFLINE']='1'
os.environ['TRANSFORMERS_OFFLINE']='1'
from app.pipeline import Pipeline
from app.cv.detector import RFDETRDetector
from app.video.sources import FileReplaySource
from app.models.contracts import LabConfig

async def benchmark(source,frames,output,mode,passes):
    weights=ROOT/'data/artifacts/models/rf-detr-small.pth'
    if not weights.is_file():raise ValueError('Existing Small weights required; downloads are disabled')
    start=time.monotonic()
    with tempfile.TemporaryDirectory(prefix='safety-throughput-') as folder:
        lab=Pipeline(folder,RFDETRDetector('small',weights=str(weights)))
        lab.config=LabConfig(target_fps=5);lab.reset()
        lab.source=FileReplaySource(str(Path(source).resolve()));lab.source.open()
        lab.session=dict(source_id='benchmark',camera_id='benchmark',session_id='benchmark',run_generation=0,schema_version='1.0',mode=mode,timestamp_ms=0,playing=False)
        processed=[];latencies=[];pass_durations=[]
        try:
            for pass_index in range(passes):
                if pass_index:
                    lab.source.close();lab.source=FileReplaySource(str(Path(source).resolve()));lab.source.open()
                    lab.session['run_generation']+=1;lab.reset();lab.segment_start=None
                current=[]
                for i in range(frames):
                    try: await lab.process_one()
                    except StopAsyncIteration: break
                    if lab.last_timestamp is None or (current and lab.last_timestamp==current[-1]):break
                    current.append(lab.last_timestamp);processed.append(lab.last_timestamp);latencies.append(lab.metrics.inference_latency_ms)
                if lab.segment_task:await lab.segment_task
                pass_durations.append((current[-1]-current[0])/1000 if len(current)>1 else 0)
        finally:lab.source.close()
        elapsed=time.monotonic()-start;duration=sum(pass_durations)
        report={'kind':'bounded_local_cv_and_ffmpeg_replay','mode':mode,'passes':passes,'pass_source_seconds':pass_durations,'frames':len(processed),'source_seconds':duration,
            'wall_seconds_including_model_load':elapsed,'frames_per_wall_second':len(processed)/elapsed,'source_seconds_per_wall_second':duration/elapsed,
            'inference_ms_mean':sum(latencies)/len(latencies) if latencies else None,'target_fps':5,'person_threshold':.65,
            'provider_requests':0,'api_cost_usd':0,'compute_cost_usd':None,'storage_cost_usd':None,
            'rolling_segments':len(lab.repository.list('rolling_segment')),'segment_gaps':lab.repository.list('segment_gap'),
            'detector':lab.detector.provenance,'limitations':['Bounded repeated-source run including cold model load; not five-camera performance. Repeated passes are not new independent footage.',
            'Unlabeled clip; this run measures throughput, not detection or hazard accuracy.','Temporary evidence is discarded; source footage and models are preserved.']}
        path=Path(output);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(report,indent=2));print(json.dumps({k:v for k,v in report.items() if k!='detector'},indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('source');p.add_argument('--frames',type=int,default=50);p.add_argument('--passes',type=int,default=1);p.add_argument('--output',required=True);p.add_argument('--mode',choices=['offline','live_simulation'],default='live_simulation');a=p.parse_args()
    if not 2<=a.frames<=1000:p.error('--frames must be 2..1000')
    if not 1<=a.passes<=20:p.error('--passes must be 1..20')
    asyncio.run(benchmark(a.source,a.frames,a.output,a.mode,a.passes))
