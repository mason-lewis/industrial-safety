"""Score matched, human-labeled hosted exports; refuse fixtures and unmatched inputs."""
import argparse,json,sys,statistics
from datetime import datetime
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT/'backend'),str(ROOT/'backend/src')]
from app.evals.events import evaluate

from typing import get_args
from safety_app.domain.video_safety import SafetyCategory
CATEGORIES=get_args(SafetyCategory)

def score(labels,manifest,reports,workflow):
    hashes={c['sha256']:c['id'] for c in manifest['clips']}
    expected={c['clip_id'] for c in labels['clips']}
    found={};predictions=[];truth=[];abstentions=[];coverage=[];latencies=[];costs=[]
    for path in reports:
        doc=json.loads(path.read_text());run=doc['run']
        if run['mode']!='hosted':raise ValueError('Fixture exports are not product-evaluation evidence')
        clip=hashes.get(run['source_hash'])
        if clip not in expected or clip in found:raise ValueError('Unexpected or repeated source in evaluation')
        found[clip]=doc
        coverage.append({'clip_id':clip,'completed_windows':doc['completed_windows'],'total_windows':doc['total_windows'],'complete':doc['coverage_complete']})
        unknown=doc['usage']['unknown_cost_requests']
        costs.append(None if unknown else doc['usage']['usage_cost_usd'])
        times=[w['updated_at'] for w in doc['windows'] if w['status']=='completed']
        if times:latencies.append((max(datetime.fromisoformat(t) for t in times)-datetime.fromisoformat(run['created_at'])).total_seconds())
        for item in doc['safety']['cases']:
            case=item['case'];assessment=item.get('assessment')
            accepted=workflow=='screening_only' or (assessment and any(f['status']=='reviewed' and f['classification']!='insufficient_evidence' for f in assessment['findings']) and not assessment['challenge'].get('issues'))
            if not accepted:
                abstentions.append({'clip_id':clip,'case_id':case['id'],'reason':'pending, challenged or insufficient evidence'});continue
            predictions.append({'id':case['id'],'source_id':clip,'camera_id':'evaluation','session_id':'heldout','event_type':case['category'],'start_ms':case['start_ms'],'end_ms':case['end_ms']})
    if set(found)!=expected:raise ValueError('Both workflows need exactly one report per independently labeled clip')
    for clip in labels['clips']:
        if clip['label'] not in ('positive','negative','ambiguous'):raise ValueError('Unlabeled candidate')
        if clip['label']=='positive' and not clip['events']:raise ValueError('Positive clips need temporal event labels')
        for index,event in enumerate(clip['events']):
            if event['category'] not in CATEGORIES:raise ValueError('Unknown event category')
            truth.append({'id':f"{clip['clip_id']}:{index}",'source_id':clip['clip_id'],'camera_id':'evaluation','session_id':'heldout','event_type':event['category'],'start_ms':event['start_ms'],'end_ms':event['end_ms'],'uncertain':clip['label']=='ambiguous' or event.get('uncertain',False)})
    result=evaluate({'labels':truth,'predictions':predictions,'temporal_iou_threshold':.5,'mode':workflow,'coverage':[]})
    result.update(coverage=coverage,abstentions=abstentions,unknown_screening_checks=sum(s['check']['result']=='unknown' for d in found.values() for s in d['safety']['screenings']),median_screening_completion_seconds=statistics.median(latencies) if latencies else None,
        known_usage_cost_usd=sum(c for c in costs if c is not None),unknown_cost_runs=sum(c is None for c in costs),
        per_run_usage_cost_usd=costs,source_group_independence_verified=all(c.get('source_group') and 'unresolved' not in c['source_group'] for c in labels['clips']),
        limitations=['Short candidate clips do not establish false alarms per camera-hour.','A shared source recording may correlate clip errors.','Full predictions require a reviewed substantive finding and no challenge issues; pending cases are abstentions and can reduce recall.'])
    return result,found

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--labels',type=Path,required=True);parser.add_argument('--baseline',type=Path,nargs='+',required=True);parser.add_argument('--full',type=Path,nargs='+',required=True)
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    labels=json.loads(args.labels.read_text());manifest=json.loads((ROOT/'demo/manifest.json').read_text())
    baseline,b=score(labels,manifest,args.baseline,'screening_only');full,f=score(labels,manifest,args.full,'full')
    for clip in b:
        for key in ('source_hash','start_ms','end_ms','configuration'):
            if b[clip]['run'][key]!=f[clip]['run'][key]:raise ValueError('Unmatched source, interval or model/budget configuration: '+clip)
        for key in ('question','required_ppe','jurisdiction','review_strategy'):
            if b[clip]['run']['request'][key]!=f[clip]['run']['request'][key]:raise ValueError('Unmatched request: '+clip)
        if b[clip]['run']['request']['escalate_to_safety_team'] or not f[clip]['run']['request']['escalate_to_safety_team']:
            raise ValueError('Baseline must disable specialists and full must enable them')
    result={'data_kind':'human_labeled_hosted_exports','matching_threshold':.5,'baseline':baseline,'full':full,'pilot_trials':labels.get('trials',[])}
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,indent=2)+'\n');print(args.output)
