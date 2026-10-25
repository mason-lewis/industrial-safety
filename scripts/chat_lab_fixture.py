#!/usr/bin/env python3
"""Isolated Chat Lab fixture; never enables or calls a hosted provider."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'backend'),str(ROOT/'backend/src')]
os.environ.update(SAFETY_CHAT_TESTING='1',SAFETY_PAID_REVIEW='0',AI_ALWAYS_ON='false',AI_ENABLED='false',OPENAI_API_KEY='')
from pydantic_ai import models
models.ALLOW_MODEL_REQUESTS=False
from safety_app.infrastructure.runtime import Runtime
from safety_app.modules.safety_review.lab_service import ChatLab,CASES
from safety_app.domain.chat_lab import LabComparisonInput
from safety_app.api.auth import Operator
from safety_app.workers.runner import execute

async def main():
    root=Path(tempfile.mkdtemp(prefix='safety-chat-fixture-'))
    shutil.copytree(ROOT/'configs',root/'configs')
    for name in ('README.md','docs/chat-lab.md'):
        target=root/name;target.parent.mkdir(exist_ok=True,parents=True);shutil.copy2(ROOT/name,target)
    rt=Runtime(root);lab=ChatLab(rt);report=[]
    for case in CASES:
        value=lab.submit(LabComparisonInput(question=case.question,test_case_id=case.id,idempotency_key=case.id),Operator())
        job=rt.jobs.claim(['chat_comparison']);await execute(rt,job)
        result=lab.get(value.id,'local')
        report.append({'case':case.id,'job':result.job.status,'states':[b.state for b in result.branches],'errors':[b.error for b in result.branches],'events':len(lab.events(value.id,'local'))})
    with rt.db.connect() as db:paid=db.execute('SELECT count(*) FROM reservations').fetchone()[0]
    receipt={'root':str(root),'provider_requests':paid,'cases':report};(root/'receipt.json').write_text(json.dumps(receipt,indent=2));print(json.dumps(receipt,indent=2))
    assert paid==0
    assert all(all(s in ('completed','blocked') for s in row['states']) for row in report if row['case']!='interrupted')

if __name__=='__main__':asyncio.run(main())
