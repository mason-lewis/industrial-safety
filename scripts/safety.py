"""Local development commands. Mock replay never contacts providers or downloads weights."""
import argparse
import asyncio
import json
from pathlib import Path
from safety_app.infrastructure.runtime import runtime,Runtime
from safety_app.domain.models import ReplayRequest

def main():
    from safety_app.infrastructure.environment import load_environment
    load_environment()
    parser=argparse.ArgumentParser();sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('migrate');sub.add_parser('sources');sub.add_parser('audit-export')
    reset=sub.add_parser('reset-trials');reset.add_argument('--apply',action='store_true');reset.add_argument('--name',default='Trial')
    restore=sub.add_parser('restore-trial');restore.add_argument('archive_id')
    sub.add_parser('trial-archives')
    reconcile=sub.add_parser('reconcile');reconcile.add_argument('reservation_id');reconcile.add_argument('--actual-usd',type=float);reconcile.add_argument('--unknown',action='store_true')
    worker=sub.add_parser('worker');worker.add_argument('kind',choices=['monitoring','review','training']);worker.add_argument('--once',action='store_true')
    replay=sub.add_parser('replay');replay.add_argument('--source-id',required=True);replay.add_argument('--start-ms',type=int,default=0);replay.add_argument('--end-ms',type=int,required=True);replay.add_argument('--profile',default='camera-1');replay.add_argument('--key',required=True)
    exports=sub.add_parser('export');exports.add_argument('dataset_id');exports.add_argument('--format',choices=['coco','yolo'],default='coco')
    evaluation=sub.add_parser('evaluate');evaluation.add_argument('document',type=Path);evaluation.add_argument('--output',type=Path,required=True)
    demo=sub.add_parser('fixture-replay');demo.add_argument('--output',type=Path,default=None)
    args=parser.parse_args()
    if args.command=='fixture-replay':
        from safety_app.workflows.fixtures import fixture_replay
        from safety_app.domain.models import uid
        output=args.output or Path('/tmp/safety-trials')/uid()
        print(json.dumps(asyncio.run(fixture_replay(output)),indent=2));return
    rt=runtime()
    if args.command in ('reset-trials','restore-trial','trial-archives'):
        from safety_app.infrastructure.trials import Trials
        trials=Trials(rt)
        if args.command=='reset-trials':
            plan=trials.preview()
            result=trials.reset(plan.plan_id,args.name,'local-cli') if args.apply else plan
            print(result.model_dump_json(indent=2))
        elif args.command=='restore-trial':print(trials.restore(args.archive_id,'local-cli').model_dump_json(indent=2))
        else:print(json.dumps([a.model_dump(mode='json') for a in trials.list()],indent=2))
    elif args.command=='reconcile':
        if args.actual_usd is None and not args.unknown:parser.error('Supply --actual-usd from provider usage, or --unknown to retain the charge reservation')
        rt.budgets.reconcile(args.reservation_id,args.actual_usd);print(json.dumps(rt.budgets.status(),indent=2))
    elif args.command=='migrate':
        from safety_app.infrastructure.migration import migrate
        print(json.dumps(migrate(rt),indent=2))
    elif args.command=='sources':
        from app.pipeline import Pipeline
        lab=Pipeline(rt.root);print(json.dumps(lab.discover(),indent=2));lab.repository.db.close();lab.evidence.db.close()
    elif args.command=='worker':
        from safety_app.workers.runner import worker
        asyncio.run(worker(rt,args.kind,args.once))
    elif args.command=='replay':
        from safety_app.modules.monitoring.service import Monitoring
        job=Monitoring(rt).replay_video(ReplayRequest(source_id=args.source_id,start_ms=args.start_ms,end_ms=args.end_ms,profile_id=args.profile,idempotency_key=args.key));print(job.model_dump_json(indent=2))
    elif args.command=='audit-export':rt.audit.export(force=True);print(rt.root/'data/audit/audit.json')
    elif args.command=='export':
        from safety_app.modules.labeling.export import export_dataset
        print(export_dataset(rt,args.dataset_id,args.format))
    elif args.command=='evaluate':
        from app.evals.events import evaluate
        result=evaluate(json.loads(args.document.read_text()));args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,indent=2));print(args.output)
if __name__=='__main__':main()
