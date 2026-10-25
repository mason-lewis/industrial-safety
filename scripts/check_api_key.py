"""Explicit credential probe. No key values, footage or project prompts are transmitted."""
import argparse
import json
import os
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'));sys.path.insert(0,str(ROOT/'backend/src'))
from dotenv import dotenv_values
from openai import OpenAI,APIStatusError
from app.agents.model_policy import HOSTED_MODEL
from safety_app.infrastructure.audit import Audit
from safety_app.domain.models import uid

def main():
    parser=argparse.ArgumentParser(description='Verify the configured key against the pinned OpenAI model. Generation requires explicit --allow-billable.')
    parser.add_argument('--allow-billable',action='store_true');args=parser.parse_args()
    key=os.environ.get('OPENAI_API_KEY') or dotenv_values(ROOT/'.env').get('OPENAI_API_KEY')
    if not key:print(json.dumps({'key_present':False}));return 1
    audit=Audit(ROOT/'data/audit');run=uid()
    audit.write('credential_probe_started',run_id=run,workflow_name='credential_probe',requested_model=HOSTED_MODEL,configuration={'live_probe':True,'generation_authorized':args.allow_billable,'max_output_tokens':64 if args.allow_billable else 0,'sdk_retries':0})
    try:
        with OpenAI(api_key=key,base_url='https://api.openai.com/v1',max_retries=0,timeout=20) as client:
            if args.allow_billable:
                response=client.responses.create(model=HOSTED_MODEL,input='Reply exactly OK.',reasoning={'effort':'none'},max_output_tokens=64,store=False)
                result={'authentication':'successful','requested_model':HOSTED_MODEL,'resolved_model':response.model,'status':response.status,'response':response.output_text,'usage':response.usage.model_dump() if response.usage else None}
                audit.write('credential_probe_finished',run_id=run,workflow_name='credential_probe',requested_model=HOSTED_MODEL,resolved_model=response.model,output={'status':response.status,'text':response.output_text},resources={'usage':result['usage'],'cost_usd':None,'cost_status':'unpriced credential probe; not included in application reservation totals','provider_request_id':response._request_id})
                print(json.dumps(result))
                return 0 if response.status=='completed' and response.output_text.strip()=='OK' and response.model==HOSTED_MODEL else 1
            model=client.models.retrieve(HOSTED_MODEL)
            result={'authentication':'successful','model_access':model.id,'generation_tested':False}
            audit.write('credential_probe_finished',run_id=run,workflow_name='credential_probe',output=result)
            print(json.dumps(result));return 0
    except APIStatusError as exc:
        result={'http_status':exc.status_code,'error_code':exc.code,'generation_tested':args.allow_billable}
        audit.write('credential_probe_failed',status='failed',run_id=run,workflow_name='credential_probe',output=result)
        print(json.dumps(result));return 1
    except Exception as exc:
        result={'error_type':type(exc).__name__}
        audit.write('credential_probe_failed',status='failed',run_id=run,workflow_name='credential_probe',output=result)
        print(json.dumps(result));return 1

if __name__=='__main__':raise SystemExit(main())
