from safety_app.domain.base import StrictModel as BaseModel
from fastapi import APIRouter,Depends
from pydantic import Field
from typing import Literal
from safety_app.api.auth import operator,Operator
from safety_app.domain.models import Annotation,DatasetVersion,ClassRegistry,Job,DeploymentRecord
from safety_app.agents.annotation_assistant.models import AnnotationRequest
from safety_app.infrastructure.runtime import runtime
from safety_app.modules.labeling.service import Labeling
router=APIRouter(prefix='/api')
@router.get('/classes',response_model=ClassRegistry)
def classes():return runtime().registry
@router.post('/annotations',response_model=Annotation)
def annotation(body:Annotation,user:Operator=Depends(operator)):return Labeling(runtime(),site_id=user.site_id).annotate(body)
class Approvals(BaseModel):annotation_ids:list[str]=Field(min_length=1,max_length=1000)
@router.post('/annotations/approve',response_model=list[Annotation])
def approve(body:Approvals,user:Operator=Depends(operator)):return Labeling(runtime(),site_id=user.site_id).approve_annotations(body.annotation_ids,user.id)
class Assistance(AnnotationRequest):idempotency_key:str
@router.post('/annotations/assist',response_model=Job)
def assist(body:Assistance,user:Operator=Depends(operator)):
    rt=runtime()
    for fid in body.frame_ids:
        frame=rt.db.get('frame',fid);media=rt.db.get('media',frame['media_id'])
        if rt.camera(media['camera_id']).site_id!=user.site_id:raise ValueError('Frame outside site')
    return rt.jobs.start('annotation_assistance',body.idempotency_key,body.model_dump(exclude={'idempotency_key'}),user.site_id)
class DatasetInput(BaseModel):
    approved_annotation_ids:list[str]=Field(min_length=1,max_length=10000)
    split_manifest:dict[str,Literal['train','valid','test']]
@router.post('/datasets/versions',response_model=DatasetVersion)
def dataset(body:DatasetInput,user:Operator=Depends(operator)):return Labeling(runtime(),site_id=user.site_id).create_dataset_version(body.approved_annotation_ids,body.split_manifest)
@router.get('/datasets/versions',response_model=list[DatasetVersion])
def datasets():return runtime().db.list('dataset')
@router.get('/datasets/{id}/export')
def export(id:str,format:Literal['coco','yolo']='coco'):
    from safety_app.modules.labeling.export import export_dataset
    from fastapi.responses import Response
    import io,zipfile
    folder=export_dataset(runtime(),id,format);out=io.BytesIO()
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as zip:
        for path in folder.rglob('*'):
            if path.is_file():zip.write(path,str(path.relative_to(folder)))
    return Response(out.getvalue(),media_type='application/zip')
class TrainingInput(BaseModel):
    dataset_id:str
    idempotency_key:str
    epochs:int=Field(default=30,ge=1,le=500)
    batch_size:int=Field(default=2,ge=1,le=64)
    device:Literal['cpu','mps','cuda']='cpu'
    remote:bool=False
@router.post('/models/train',response_model=Job)
def train(body:TrainingInput,user:Operator=Depends(operator)):
    runtime().db.get('dataset',body.dataset_id)
    if body.remote:raise ValueError('Remote training disabled')
    return runtime().jobs.start('train',body.idempotency_key,body.model_dump(exclude={'idempotency_key'}),user.site_id)
class EvaluateInput(BaseModel):
    idempotency_key:str
    device:Literal['cpu','mps','cuda']='cpu'
@router.post('/models/{id}/evaluate',response_model=Job)
def evaluate(id:str,body:EvaluateInput,user:Operator=Depends(operator)):
    runtime().db.get('model',id)
    return runtime().jobs.start('evaluate',body.idempotency_key,{'model_id':id,'device':body.device},user.site_id)
class PromoteInput(BaseModel):
    evaluation_id:str
    camera_scope:list[str]=Field(min_length=1,max_length=100)
@router.post('/models/{id}/promote',response_model=DeploymentRecord)
async def promote(id:str,body:PromoteInput,user:Operator=Depends(operator)):
    from app.main import pipeline
    return await Labeling(runtime(),site_id=user.site_id).promote_model(id,body.evaluation_id,body.camera_scope,user.id,pipeline)

class RegisterInput(BaseModel):
    license:str=Field(min_length=1,max_length=400)
    device:Literal['cpu','mps','cuda']='cpu'
    idempotency_key:str
@router.post('/models/{id}/register',response_model=Job)
def register(id:str,body:RegisterInput,user:Operator=Depends(operator)):
    return runtime().jobs.start('register',body.idempotency_key,{'model_id':id,'license':body.license,'device':body.device},user.site_id)
@router.get('/annotations',response_model=list[Annotation])
def annotations():return runtime().db.list('annotation')
@router.get('/label-queue')
def label_queue():return runtime().db.list('label_queue')

@router.put('/classes',response_model=ClassRegistry)
def save_classes(body:ClassRegistry,user:Operator=Depends(operator)):
    import json,os,tempfile
    rt=runtime();rt.db.save('class_registry',{'id':body.version,'registry':body.model_dump(mode='json')},immutable=True)
    path=rt.root/'configs/classes.yaml';fd,name=tempfile.mkstemp(dir=path.parent,prefix='.classes-')
    with os.fdopen(fd,'w') as stream:stream.write(body.model_dump_json(indent=2))
    os.replace(name,path);rt.registry=body;rt.audit.write('classes_versioned',output=body.model_dump(mode='json'));return body
