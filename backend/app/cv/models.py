"""Registry of operator-installed local checkpoints. No remote weights or pickle fallback."""
import hashlib
import json
import re
from pathlib import Path

def resolve_model(root,id):
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}',id):raise ValueError('Invalid model ID')
    folder=(Path(root)/'data/artifacts/models').resolve();path=(folder/f'{id}.detector.json').resolve()
    if not path.is_relative_to(folder) or not path.is_file():raise ValueError('Registered model manifest not found')
    manifest=json.loads(path.read_text())
    if not isinstance(manifest,dict):raise ValueError('Model manifest must be an object')
    if manifest.get('id')!=id or not manifest.get('license') or manifest.get('format')!='rfdetr-checkpoint':raise ValueError('Model needs matching ID, RF-DETR format and declared license')
    if not isinstance(manifest.get('weights'),str):raise ValueError('Weights filename must be a string')
    weights=(folder/manifest['weights']).resolve()
    if not weights.is_relative_to(folder) or not weights.is_file() or weights.suffix not in ('.pth','.ckpt'):raise ValueError('Weights must be a local checkpoint inside data/artifacts/models')
    expected=manifest.get('sha256','')
    with weights.open('rb') as stream:actual=hashlib.file_digest(stream,'sha256').hexdigest()
    if actual!=expected:raise ValueError('Checkpoint checksum differs from the registered model')
    labels=manifest.get('classes',[])
    if not isinstance(labels,list) or not labels or len(labels)>200 or any(not isinstance(label,str) or not label.strip() for label in labels) or len(set(labels))!=len(labels):raise ValueError('Model classes must be unique explicit names')
    return manifest,weights,{i:label for i,label in enumerate(labels)}

def model_catalog(root):
    folder=Path(root)/'data/artifacts/models';rows=[]
    for path in sorted(folder.glob('*.detector.json')):
        id=path.name.removesuffix('.detector.json')
        try:
            manifest,_,_=resolve_model(root,id)
            rows.append({'id':id,'name':manifest.get('name',id),'classes':manifest['classes'],'license':manifest['license'],'status':'registered; compatibility checked on activation'})
        except (ValueError,OSError,TypeError):
            rows.append({'id':id,'name':id,'classes':[],'status':'invalid manifest or checksum'})
    return rows
