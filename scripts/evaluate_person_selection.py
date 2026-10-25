"""Free, read-only threshold audit against reviewed validation images and frozen local proposals."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend/src'))
from app.trainer.assistance import iou


def evaluate(project_path):
    path=Path(project_path);project=json.loads(path.read_text());clips={c['id']:c for c in project['clips']}
    frames=[];skipped=[]
    for frame in project['frames']:
        if not frame.get('reviewed') or clips[frame['clip_id']]['split']!='valid':continue
        run=frame.get('proposal_run',{});image=path.parent/'images'/frame['file_name']
        if run.get('confidence',1)>.5 or not image.is_file() or hashlib.sha256(image.read_bytes()).hexdigest()!=run.get('image_sha256'):
            skipped.append(frame['id']);continue
        frames.append(frame)
    results=[]
    for threshold in [.5,.6,.65,.7,.8]:
        tp=fp=fn=0;examples=[]
        for frame in frames:
            truth=[b for b in frame['boxes'] if b['label']=='person'];unmatched=set(range(len(truth)));false=[]
            for box in sorted([b for b in frame.get('proposals',[]) if b['label']=='person' and b['score']>=threshold],key=lambda b:-b['score']):
                best=max(unmatched,key=lambda i:iou(box['bbox'],truth[i]['bbox']),default=None)
                if best is not None and iou(box['bbox'],truth[best]['bbox'])>=.5:tp+=1;unmatched.remove(best)
                else:fp+=1;false.append(box)
            fn+=len(unmatched)
            examples.append({'frame_id':frame['id'],'timestamp_ms':frame['timestamp_ms'],'false_detections':false,'missed_people':[truth[i] for i in sorted(unmatched)]})
        results.append({'threshold':threshold,'true_positive':tp,'false_positive':fp,'false_negative':fn,
                        'precision':tp/(tp+fp) if tp+fp else None,'recall':tp/(tp+fn) if tp+fn else None,'examples':examples})
    return {'evaluation':'Existing local RF-DETR proposals against current human-reviewed validation labels','frames':len(frames),
            'frame_ids':[f['id'] for f in frames],'skipped_frames':skipped,'results':results,'provider_requests':0,
            'project_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'limitations':['Small correlated validation sample; no held-out test use or safety accuracy claim.',
                           'Frozen detector proposals at their recorded inference floor; no new inference or tracking throughput measurement.',
                           'Labels were detector/AI assisted and human saved; independent corrections may be incomplete.',
                           'IoU 0.5 matching measures localization as well as human detection; inspect false/missed examples.']}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('project');parser.add_argument('--output',required=True);args=parser.parse_args()
    report=evaluate(args.project);output=Path(args.output);output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(report,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k!='results'},indent=2))
    for r in report['results']:print({k:v for k,v in r.items() if k!='examples'})
