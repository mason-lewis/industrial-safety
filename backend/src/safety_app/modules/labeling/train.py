"""Local RF-DETR job adapter. Remote training has no dispatch implementation."""
import asyncio
import json
import os
import shutil
import sys
from pathlib import Path
from safety_app.modules.labeling.export import export_dataset

async def train(rt,job):
    try:
        rt.db.get('test_seal',job.payload['dataset_id']);raise ValueError('This dataset has exposed its test results. Create a new independent dataset')
    except KeyError:pass
    if job.payload.get('remote'):raise ValueError('Remote training is disabled. No cloud job was dispatched')
    from app.trainer.workspace import LearningWorkspace
    if not LearningWorkspace(rt.root).dependencies()['ready']:raise ValueError('RF-DETR training dependencies are missing')
    folder=rt.root/'data/models'/job.id;folder.mkdir(parents=True,exist_ok=True)
    dataset=await asyncio.to_thread(export_dataset,rt,job.payload['dataset_id'],'coco');training=folder/'train-data';training.mkdir(exist_ok=True)
    for split in ('train','valid'):
        if not (training/split).exists():await asyncio.to_thread(shutil.copytree,dataset/split,training/split)
    (training/'split-provenance.json').write_text(json.dumps({'test_present':False,'human_review_confirmed':True,'dataset_id':job.payload['dataset_id']}))
    classes=[c.name for c in rt.classes(rt.db.get('dataset',job.payload['dataset_id'])['class_registry_version']).classes]
    cmd=[sys.executable,str(rt.root/'backend/app/trainer/cli/train.py'),'--dataset',str(training),'--output',str(folder/'output'),'--device',job.payload['device'],'--epochs',str(job.payload['epochs']),'--batch-size',str(job.payload['batch_size']),'--run-training','--required-classes',*classes]
    env={k:v for k,v in os.environ.items() if not any(s in k.upper() for s in ('KEY','TOKEN','SECRET','PASSWORD'))};env['AI_ENABLED']='false'
    with (folder/'process.log').open('wb') as stream:
        process=await asyncio.create_subprocess_exec(*cmd,cwd=rt.root,stdout=stream,stderr=stream,env=env,start_new_session=True)
        try:
            async with asyncio.timeout(rt.config['training']['max_seconds']):
                while process.returncode is None:
                    if rt.jobs.cancelled(job.id):raise asyncio.CancelledError()
                    rt.jobs.heartbeat(job.id)
                    try:await asyncio.wait_for(process.wait(),1)
                    except TimeoutError:pass
            if process.returncode:raise RuntimeError(f'Training failed with exit code {process.returncode}; inspect process.log')
        finally:
            if process.returncode is None:
                import signal
                os.killpg(process.pid,signal.SIGTERM)
                try:await asyncio.wait_for(process.wait(),5)
                except TimeoutError:os.killpg(process.pid,signal.SIGKILL);await process.wait()
    paths=list((folder/'output').rglob('*.pth'))
    if not paths:raise ValueError('Training produced no checkpoint')
    from safety_app.domain.models import ModelVersion
    model=ModelVersion(version=job.id,weights_ref=str(paths[0].relative_to(rt.root)),dataset_id=job.payload['dataset_id'])
    rt.db.save('model',model,immutable=True);return [model.id]

async def register_model(rt,job):
    model=rt.db.get('model',job.payload['model_id'])
    evaluations=[e for e in rt.db.list('model_evaluation') if e['model_id']==model['id'] and e['status']=='completed']
    if not evaluations:raise ValueError('Complete held-out evaluation before registration')
    dataset=rt.db.get('dataset',model['dataset_id']);classes=rt.classes(dataset['class_registry_version']).classes
    registered_ref='candidate-'+model['id'];weights=(rt.root/model['weights_ref']).resolve()
    if not weights.is_relative_to(rt.root.resolve()) or not weights.is_file():raise ValueError('Missing local checkpoint')
    cmd=[sys.executable,str(rt.root/'backend/app/trainer/cli/register.py'),'--weights',str(weights),'--classes',*[c.name for c in classes],'--id',registered_ref,'--name',registered_ref,'--license',job.payload['license'],'--device',job.payload['device']]
    process=await asyncio.create_subprocess_exec(*cmd,cwd=rt.root,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.STDOUT)
    try:
        async with asyncio.timeout(90):output,_=await process.communicate()
        if process.returncode:raise ValueError('Checkpoint registration failed: '+output.decode(errors='replace')[-1000:])
    finally:
        if process.returncode is None:process.terminate();await process.wait()
    rt.db.save('model_registration',{'id':model['id'],'registered_ref':registered_ref},immutable=True)
    rt.audit.write('model_registered',job_id=job.id,provenance={'model_id':model['id'],'registered_ref':registered_ref});return [model['id']]
