"""Explicit local plumbing demo, never a generated answer to the user's question."""
import json
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.messages import ModelResponse,ToolCallPart,ToolReturnPart,UserPromptPart

def returns(messages,tool):
    return [p.content for m in messages for p in m.parts if isinstance(p,ToolReturnPart) and p.tool_name==tool]
def user_objects(messages):
    for m in messages:
        for p in m.parts:
            if isinstance(p,UserPromptPart):
                for v in ([p.content] if isinstance(p.content,str) else p.content):
                    if isinstance(v,str) and v.startswith('{'):yield json.loads(v)

def data(value):return value.model_dump(mode='json') if hasattr(value,'model_dump') else json.loads(value) if isinstance(value,str) else value

def models():
    def factory(name):
        async def respond(messages,info):
            output=info.output_tools[0].name
            if name=='safety_reviewer':
                done=[data(v) for v in returns(messages,'delegate_chat_specialist')]
                if not done:
                    return ModelResponse(parts=[ToolCallPart('delegate_chat_specialist',{'task':{'question_id':'coverage','specialist':'applicability','question':'Explain federal employer coverage and missing applicability facts.'}},tool_call_id='fixture-app'),ToolCallPart('delegate_chat_specialist',{'task':{'question_id':'vehicle','specialist':'vehicles','question':'Find the exact forklift clause 1910.178(m)(2) for this local retrieval demonstration.'}},tool_call_id='fixture-vehicle')])
                claims=[c for r in done for c in r['claims']]
                return ModelResponse(parts=[ToolCallPart(output,{'answer':'Local fixture completed: the boss delegated applicability and vehicles, exercised source retrieval, and sent the claims to independent challenge. This is a plumbing test; it does not answer your safety question.','claims':claims,'missing_context':sorted({g for r in done for g in r['missing_context']})})])
            if name.startswith('specialist_'):
                initial=next(user_objects(messages))
                task=initial['task'];specialty=task['specialist'];q=task['question_id']
                found=returns(messages,'search_safety_sources');fetched=returns(messages,'fetch_sources')
                if not found:return ModelResponse(parts=[ToolCallPart('search_safety_sources',{'query':'1910.178(m)(2)' if specialty=='vehicles' else 'employer coverage','jurisdiction':'US-federal-OSHA','topic':'','limit':1},tool_call_id='fixture-search')])
                candidates=data(found[-1])['candidates']
                if candidates and not fetched:return ModelResponse(parts=[ToolCallPart('fetch_sources',{'source_ids':[candidates[0]['id']]},tool_call_id='fixture-fetch')])
                packets=[data(v) for v in fetched]
                # Prepared packets are separate exact user content supplied by the coordinator.
                for v in user_objects(messages):
                    if 'excerpts' in v and 'gaps' in v:packets.append(v)
                refs=sorted({s['id'] for packet in packets for s in packet['excerpts']})[:5]
                gaps=sorted({g for packet in packets for g in packet['gaps']})
                return ModelResponse(parts=[ToolCallPart(output,{'specialist':specialty,'question_id':q,'answer':'Fictional fixture: source tools executed; no real safety judgment was made.','claims':[{'id':q+':fixture','text':'This local fixture demonstrates retrieval and delegation only.','kind':'uncertain','source_refs':refs}],'missing_context':gaps+['No event or violation was assessed in this fixture.']})])
            if name=='challenge_reviewer':
                v=next(user_objects(messages))
                return ModelResponse(parts=[ToolCallPart(output,{'checked_claim_ids':[c['id'] for c in v['response']['claims']],'limitations':['Fixture challenge checks the demonstration claims only; no safety accuracy was tested.']})])
            raise ValueError('Unsupported fixture agent')
        return FunctionModel(respond)
    return factory
