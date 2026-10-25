"""Opt-in official weight download and blank-image real inference. No accuracy claims."""
import argparse
import json
from pathlib import Path
import sys
import time
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.cv.detector import RFDETRDetector
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--variant',choices=['small','nano'],default='small')
parser.add_argument('--device',choices=['auto','cpu','mps','cuda'],default='auto')
args=parser.parse_args()
model=RFDETRDetector(args.variant,device=args.device)
start=time.monotonic();model.load();load=time.monotonic()-start
start=time.monotonic();result=model.predict(np.zeros((360,640,3),dtype=np.uint8),.5)
report={'provenance':model.provenance,'load_seconds':load,'blank_inference_seconds':time.monotonic()-start,'detections':len(result),'input':'blank synthetic image; plumbing only'}
print(json.dumps(report,indent=2))
