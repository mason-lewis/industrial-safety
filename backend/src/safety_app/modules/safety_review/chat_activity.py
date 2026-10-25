"""Shared, sanitized observable activity and calculated usage for chat jobs."""
import json
from safety_app.domain.chat_lab import LabUsage,LabTelemetry
from safety_app.infrastructure.audit import redact

class BranchAudit:
    def __init__(self,audit,job,branch,node=None,api='responses'):self.audit,self.job,self.branch,self.node,self.api=audit,job,branch,node,api
    def write(self,event_type,status='ok',**fields):
        fields.setdefault('job_id',self.job.id);fields.setdefault('run_id',self.node or self.job.run_id)
        fields['provenance']={**fields.get('provenance',{}),'branch':self.branch,'api':self.api}
        return self.audit.write(event_type,status,**fields)

class ChatActivity:
    def __init__(self,rt):self.rt=rt
    def events(self,id,site_id,after=0):
        job=self.rt.jobs.get(id)
        if job.site_id!=site_id or job.kind not in ('chat_comparison','video_chat'):raise KeyError('Conversation not found')
        with self.rt.audit.db.connect() as db:rows=db.execute("SELECT sequence,body FROM audit WHERE sequence>? AND json_extract(body,'$.job_id')=? ORDER BY sequence LIMIT 5000",(after,id)).fetchall()
        result=[]
        for row in rows:
            value=json.loads(row['body']);value['sequence']=row['sequence'];provenance=value.get('provenance',{});event=value['event_type']
            # Drafts/structured outputs stay private until workflow validation. No hidden reasoning.
            if event in ('model_response','run_finished','validation_failed'):
                value['output']=None;value['arguments']=None
            status=value['status'];state={'ok':'completed','budget_exceeded':'blocked','incomplete':'failed','interrupted':'failed','transient':'failed','stopped':'blocked'}.get(status,status)
            if state not in ('queued','running','completed','blocked','cancelled','failed'):state='completed'
            if event=='job_queued':state='queued';value['arguments']={'question':job.payload['question'],'mode':job.payload['mode']}
            if value.get('error'):value['error']['message']='Stage failed; see the incomplete result.'
            value=redact(value)
            api=provenance.get('api','coordinator')
            if event.startswith('native_tool'):api='web_search'
            elif event in ('tool_started','tool_finished'):api='local'
            result.append(LabTelemetry(sequence=row['sequence'],timestamp_utc=value['timestamp_utc'],branch=provenance.get('branch'),event_type=event,state=state,node_id=value.get('run_id'),parent_id=value.get('parent_run_id'),agent=value.get('agent_name'),api=api,model=value.get('resolved_model') or value.get('requested_model'),tool=value.get('tool_name'),tool_call_id=value.get('tool_call_id'),duration_ms=value.get('duration_ms'),arguments=value.get('arguments'),result=value.get('output'),resources=value.get('resources',{}),raw=value))
        return result
    def usage(self,job,events,branch=None):
        selected=[e for e in events if branch is None or e.branch==branch];result=LabUsage(synthetic=job.payload['mode']=='fixture');ids=set()
        for event in selected:
            resources=event.resources
            if event.event_type=='model_request':result.requests+=1
            if event.event_type in ('tool_started','native_tool_started'):result.tool_calls+=1
            if event.event_type=='cache_hit':result.cache_hits+=1
            if event.event_type=='model_response' and resources.get('model_request_id') not in ids:
                ids.add(resources.get('model_request_id'))
                for key in ('input_tokens','cached_input_tokens','cache_write_tokens','output_tokens'):setattr(result,key,getattr(result,key)+(resources.get(key) or 0))
                cost=resources.get('usage_cost_usd') or 0;search=resources.get('search_usd') or 0
                result.calculated_usd+=cost;result.search_usd+=search
                if event.api=='decisions':result.decisions_usd+=cost
                else:result.responses_usd+=max(0,cost-search)
        nodes={e.node_id for e in selected if e.node_id}
        with self.rt.db.connect() as db:reservations=db.execute('SELECT run_id,amount,state FROM reservations WHERE workflow_id=? AND actual IS NULL',(job.run_id,)).fetchall()
        for row in reservations:
            if branch is None or row['run_id'] in nodes:
                result.reserved_usd+=row['amount']/1e6;result.unknown_requests+=row['state']=='unknown'
        return result
