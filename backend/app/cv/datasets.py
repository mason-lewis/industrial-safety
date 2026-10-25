import json
import re
from pathlib import Path

def dataset_folder(root,id):
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}',id):raise ValueError('Invalid dataset ID')
    base=(Path(root)/'data/training').resolve();folder=(base/id).resolve()
    if not folder.is_relative_to(base) or not (folder/'manifest.json').is_file():raise ValueError('Prepared dataset not found')
    return folder

def dataset_catalog(root):
    rows=[]
    for manifest in sorted((Path(root)/'data/training').glob('*/manifest.json')):
        try:
            folder=dataset_folder(root,manifest.parent.name);data=json.loads((folder/'manifest.json').read_text())
            rows.append({'id':folder.name,'images':len(data['frames']),'sources':len(data['sources']),'status':data['status'],'classes':data['classes']})
        except (ValueError,OSError,KeyError):continue
    return rows
