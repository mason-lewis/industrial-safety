"""Held-out detection evaluation and registered-model provenance."""
import asyncio
import json
import sys
from safety_app.domain.models import uid
from safety_app.modules.labeling.export import export_dataset
async def evaluate_model(rt,job):
    model=rt.db.get('model',job.payload['model_id']);weights=(rt.root/model['weights_ref']).resolve()
    if not weights.is_relative_to(rt.root.resolve()) or not weights.is_file():raise ValueError('Model checkpoint missing or outside project')
    dataset=await asyncio.to_thread(export_dataset,rt,model['dataset_id'],'coco');folder=rt.root/'data/models'/job.id;folder.mkdir(parents=True,exist_ok=True)
    rt.db.save('test_seal',{'id':model['dataset_id'],'model_id':model['id']},immutable=True)
    classes=[c.name for c in rt.classes(rt.db.get('dataset',model['dataset_id'])['class_registry_version']).classes]
    # The existing CLI performs real held-out RF-DETR evaluation.
    cmd=[sys.executable,str(rt.root/'backend/app/trainer/cli/train.py'),'--dataset',str(dataset),'--output',str(folder/'output'),'--device',job.payload['device'],'--evaluate-weights',str(weights),'--required-classes',*classes]
    with (folder/'process.log').open('wb') as log:
        process=await asyncio.create_subprocess_exec(*cmd,cwd=rt.root,stdout=log,stderr=log)
        try:
            async with asyncio.timeout(rt.config['training']['max_seconds']):
                while process.returncode is None:
                    if rt.jobs.cancelled(job.id):raise asyncio.CancelledError()
                    rt.jobs.heartbeat(job.id)
                    try:await asyncio.wait_for(process.wait(),1)
                    except TimeoutError:pass
            if process.returncode:raise ValueError('Evaluation failed. Inspect process.log')
        finally:
            if process.returncode is None:process.terminate();await process.wait()
    rt.db.save('test_seal',{'id':model['dataset_id'],'model_id':model['id']},immutable=True)
    metrics=json.loads((folder/'output/evaluation.json').read_text())
    record={'id':uid(),'model_id':model['id'],'dataset_id':model['dataset_id'],'status':'completed','metrics':metrics,'passed':True,'note':'Held-out evaluation completed. This flag establishes completion, not a site accuracy threshold. Promotion requires a human decision.'}
    rt.db.save('model_evaluation',record,immutable=True);rt.audit.write('evaluation_finished',job_id=job.id,output=record);return [record['id']]
