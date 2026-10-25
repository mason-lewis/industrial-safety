"""Fixed demonstration models; never assess real footage or dispatch to a provider."""
import json

from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel


def models():
    plans = 0

    def create(name):
        searched = False

        def respond(messages, info):
            nonlocal searched, plans
            contents = [part.content for message in messages for part in message.parts
                        if part.part_kind == 'user-prompt']
            request = json.loads(contents[0][0])
            properties = info.output_tools[0].parameters_json_schema['properties']
            if 'observations' in properties:
                frames = [json.loads(item) for item in contents[0][1:]
                          if isinstance(item, str) and '"timestamp_ms"' in item and '"content_hash"' in item]
                frame = frames[0]
                output = {'summary': 'Fictional demonstration observation; no recognition was performed.',
                          'observations': [{'media_id': request['media_id'], 'start_ms': frame['timestamp_ms'],
                                            'end_ms': frame['timestamp_ms'], 'description': 'Fixed fixture observation.',
                                            'evidence_refs': [frame['id']], 'uncertainty': ['Fixture only']}],
                          'limitations': ['Fixed fixture response; pixels were attached but not understood.']}
                if request.get('safety_screening'):
                    output['screening_checks']=[{'category':'blocked_access','result':'suspected','visibility':'clear','route_basis':'visible_route',
                        'condition':'Fictional blocked route used only to test the two-layer workflow.',
                        'start_ms':frame['timestamp_ms'],'end_ms':frame['timestamp_ms'],'evidence_refs':[frame['id']],
                        'needs_judgment':True,'priority':'high','exposure':'Fictional person approaching a route.',
                        'immediate_reason':'Fixture demonstrates the manager-attention record; no real alert is established.',
                        'limitations':['Fixed synthetic fixture; not an actual hazard.']}]
                if request.get('safety_screening') and 'consecutive window' in request.get('purpose',''):
                    for category in ('ppe','unstable_stack','slip_trip'):
                        output['screening_checks'].append({'category':category,'result':'unknown','visibility':'not_assessable',
                            'condition':'Fixture category; no visual assessment performed.','start_ms':frame['timestamp_ms'],
                            'end_ms':frame['timestamp_ms'],'evidence_refs':[frame['id']],
                            **({'ppe_state':'not_assessable'} if category=='ppe' else {})})
            elif 'action' in properties:
                if not searched:
                    searched = True
                    return ModelResponse(parts=[ToolCallPart('search_video_memory', {'query': '', 'limit': 3}, tool_call_id='fixture-memory')])
                plans += 1
                if request['inspections_remaining'] and plans == 1:
                    scope = request['scope']
                    output = {'action': 'inspect', 'purpose': 'Demonstrate targeted follow-up inspection',
                              'intervals': [{'start_ms': scope['start_ms'],
                                             'end_ms': min(scope['end_ms'], scope['start_ms'] + request['max_interval_ms'])}]}
                elif request['inspections_remaining'] and plans == 2:
                    scope = request['scope']
                    width = min(request['max_interval_ms'], max(1, (scope['end_ms'] - scope['start_ms']) // 2))
                    output = {'action': 'compare', 'purpose': 'Demonstrate before/after comparison',
                              'intervals': [{'start_ms': scope['start_ms'], 'end_ms': scope['start_ms'] + width},
                                            {'start_ms': scope['end_ms'] - width, 'end_ms': scope['end_ms']}]}
                else:
                    returned = [part for message in messages for part in message.parts
                                if part.part_kind == 'tool-return' and part.tool_name == 'search_video_memory']
                    chunks = json.loads(returned[-1].model_response_str())
                    observation = chunks[0]['observations'][0]
                    output = {'action': 'answer', 'purpose': 'Finish the fixture demonstration', 'intervals': [],
                              'answer': {'answer': 'Local fixture completed. These fixed responses do not answer your video question.',
                                         'claims': [{'id': 'fixture-claim', 'start_ms': observation['start_ms'],
                                                     'end_ms': observation['end_ms'], 'text': 'Fixed fixture observation.',
                                                     'evidence_refs': observation['evidence_refs'], 'uncertainty': ['Fixture only']}],
                                         'limitations': ['Not a real AI analysis.']}}
            else:
                output = {'checks': [{'claim_id': claim['id'], 'verdict': 'uncertain',
                                      'explanation': 'Fixed fixture check; no factual accuracy established.'}
                                     for claim in request['answer']['claims']],
                          'answer_supported': False, 'limitations': ['Fixture only.']}
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, output)])

        return FunctionModel(respond)
    return create
