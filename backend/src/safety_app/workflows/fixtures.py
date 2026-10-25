"""Explicitly fictional, isolated replay for first-run checks. No provider or weights."""
import asyncio
import json
import shutil
from collections import defaultdict,deque
from pathlib import Path
import cv2
import numpy as np
from pydantic_ai.models.test import TestModel
from safety_app.infrastructure.runtime import Runtime
from safety_app.workers.runner import execute

def team_fixture_models(outputs,specialist="vehicles",classification="insufficient_evidence"):
    """Deterministic real tool delegation through FunctionModel; never a provider call."""
    from copy import deepcopy
    from pydantic_ai.models.function import FunctionModel
    from pydantic_ai.messages import ModelResponse,ToolCallPart,UserPromptPart
    values=deepcopy(outputs)
    final=values['safety_reviewer']
    for finding in final.get('findings',[]):
        finding.update(question_id='fixture-event',specialist=specialist,condition=finding.get('condition',finding['conclusion']),classification=classification)
    final.setdefault('investigation',{'cause_status':'not_established','limitations':['Fictional fixture: incident cause cannot be established']})
    async def boss(messages,info):
        if not any(getattr(p,'tool_name',None)=='delegate_specialist' and getattr(p,'part_kind',None)=='tool-return' for m in messages for p in m.parts):
            request=None
            for message in messages:
                for part in message.parts:
                    if isinstance(part,UserPromptPart):
                        for item in part.content if isinstance(part.content,list) else [part.content]:
                            if isinstance(item,str):
                                try:value=json.loads(item)
                                except ValueError:continue
                                if 'observations' in value:request=value;break
            ids=[o['id'] for o in request['observations']]
            return ModelResponse(parts=[ToolCallPart('delegate_specialist',{'task':{'question_id':'fixture-scope','specialist':'applicability','question':'Which applicability facts are unknown in this fictional fixture?','observation_ids':ids}},tool_call_id='scope-call'),ToolCallPart('delegate_specialist',{'task':{'question_id':'fixture-event','specialist':specialist,'question':'Review the fictional observation and state evidence limits.','observation_ids':ids}},tool_call_id='event-call')])
        tool=info.output_tools[0]
        return ModelResponse(parts=[ToolCallPart(tool.name,final)])
    def model(name):
        if name=='safety_reviewer':return FunctionModel(boss)
        if name=='specialist_applicability':return TestModel(call_tools=[],custom_output_args={'specialist':'applicability','missing_context':[]})
        if name=='specialist_'+specialist:return TestModel(call_tools=[],custom_output_args={'specialist':specialist,'findings':final.get('findings',[]),'missing_context':final.get('missing_context',[])})
        return TestModel(call_tools=[],custom_output_args=values[name])
    return model
class FixtureDetections:
    """Only the array contract used by the fixture; no optional CV dependency."""
    def __init__(self,xyxy=None,class_id=None,confidence=None,tracker_id=None):
        self.xyxy=np.array([[2,2,15,15]],dtype=float) if xyxy is None else xyxy
        self.class_id=np.array([1]) if class_id is None else class_id
        self.confidence=np.array([.9]) if confidence is None else confidence
        self.tracker_id=np.array([1]) if tracker_id is None else tracker_id
    def __len__(self):return len(self.xyxy)
    def __getitem__(self,mask):
        return FixtureDetections(self.xyxy[mask],self.class_id[mask],self.confidence[mask],self.tracker_id[mask])

class FixtureDetector:
    labels={1:'person'}
    provenance={'version':'fixture-v1','data_kind':'fictional_plumbing_only'}
    def predict(self,image,confidence):
        return FixtureDetections()
class FixtureTracker:
    def __init__(self,**kwargs):self.trails=defaultdict(lambda:deque(maxlen=24))
    def update(self,detections,timestamp=None):return detections
async def fixture_replay(output):
    from app import pipeline as legacy
    from app.models.contracts import LabConfig,ZoneConfig,RuleConfig
    from safety_app.modules.safety_review.service import SafetyReview
    root=Path(output).resolve()
    if root.exists():raise ValueError('Use a new output directory. Existing fixture runs are preserved')
    root.mkdir(parents=True);project=Path(__file__).resolve().parents[4];shutil.copytree(project/'configs',root/'configs')
    rt=Runtime(root);(root/'data/config').mkdir(parents=True);(root/'data/input').mkdir(parents=True)
    config=LabConfig(target_fps=10,zones=[ZoneConfig(id='walkway',label='Fictional test zone',points=[(0,0),(1,0),(1,1),(0,1)])],rules=[RuleConfig(id='fixture-dwell',kind='zone_dwell',zone_id='walkway',duration_ms=400)])
    (root/'data/config/lab.json').write_text(config.model_dump_json())
    video=root/'data/input/fictional-fixture.mp4';writer=cv2.VideoWriter(str(video),cv2.VideoWriter_fourcc(*'mp4v'),10,(20,20))
    if not writer.isOpened():raise ValueError('Local fixture encoder is unavailable')
    for i in range(10):writer.write(np.full((20,20,3),i*10,dtype=np.uint8))
    writer.release()
    prior=legacy.ByteTrackAdapter;legacy.ByteTrackAdapter=FixtureTracker
    try:
        lab=legacy.Pipeline(root,detector=FixtureDetector());source=lab.discover()[0]['id'];lab.repository.db.close();lab.evidence.db.close()
        job=rt.jobs.start('replay','fictional-replay',{'source_id':source,'profile_id':'camera-1','start_ms':0,'end_ms':900})
        result=await execute(rt,job,detector=FixtureDetector())
        if result.status!='completed':raise RuntimeError(str(result.error))
        event=rt.db.list('event')[0];reviewjob=next(j for j in rt.jobs.list() if j.kind=='event_review');frame=next(f for f in rt.db.list('frame') if f['timestamp_ms']==400)
        outputs={'video_observer':{'observations':[{'media_id':job.run_id,'start_ms':400,'end_ms':400,'description':'Fictional fixture in test zone','evidence_refs':[frame['id']],'method':'ai','uncertainty':['Synthetic fixture']}],'visibility_limits':['No real safety judgment']},'safety_reviewer':{'findings':[{'id':'fictional-finding','review_id':reviewjob.run_id,'event_id':event['id'],'conclusion':'Fictional review contract check','evidence_refs':[frame['id']],'uncertainty':['No actual scene or requirement']}]},'challenge_reviewer':{'checked_finding_ids':['fictional-finding'],'issues':[],'limitations':['Fictional example']}}
        reviewed=await execute(rt,reviewjob,team_fixture_models(outputs))
        if reviewed.status!='completed':raise RuntimeError(str(reviewed.error))
        decision=SafetyReview(rt).save_human_decision('fictional-finding','uncertain','Fictional fixture only','fixture-operator','local')
        report={'data_kind':'fictional_plumbing_only','output_directory':str(root),'replay_job':job.id,'event_id':event['id'],'review_job':reviewjob.id,'finding_id':'fictional-finding','decision_id':decision.id,'specialist_calls':len(rt.db.get('review',reviewjob.run_id)['delegations']),'provider_requests':0,'accuracy_claim':None}
        (root/'report.json').write_text(json.dumps(report,indent=2));return report
    finally:legacy.ByteTrackAdapter=prior
