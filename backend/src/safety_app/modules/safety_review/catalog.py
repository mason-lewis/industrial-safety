"""Single ownership registry. Unknown domains stay unmapped, never disappear."""
import json
import re

PROFILES = {
    'applicability': 'Activity, employer/worker coverage, jurisdiction, event-date rules, General Duty Clause elements, reporting/recordkeeping. Resolve unmapped questions and missing sources.',
    'vehicles': 'Vehicles, forklifts, pedestrian interactions, cranes, rigging, load handling and storage. Own task-specific training. Walking surfaces are surfaces; energy isolation is energy.',
    'surfaces': 'Walking surfaces, slips/trips, ladders, stairs, edges, platforms, collective fall protection, dockboards and excavation stability. Personal protective systems are PPE.',
    'machinery': 'Machine guards, entanglement, pinch/cut/crush points and tool operation. Servicing isolation is energy, even for these machines.',
    'energy': 'Electrical installations/work, shock/arc hazards and lockout/tagout for every energy source. Electrical PPE is PPE.',
    'health': 'Chemical/biological exposure, dust, noise, heat, radiation, ergonomics, hazard communication, process hazards and confined-space entry requirements. Personal protection is PPE.',
    'emergency': 'Fire prevention/ignition controls, extinguishers, exits, alarms, evacuation, rescue readiness and medical/first-aid readiness.',
    'ppe': 'PPE hazard assessment, selection, rating, fit, condition and use: respiratory, electrical and personal fall protection included. Request hazard facts from the responsible domain.',
}
_GROUPS = {
    'vehicles': ['1910.176','1910.177','1910.178','1910.179','1910.180','1910.181','1910.184','1926.250','1926.251','1926.602'],
    'surfaces': ['1910.21','1910.22','1910.23','1910.24','1910.25','1910.26','1910.27','1910.28','1910.29','1910.30','1910.66','1910.67','1910.68','1926.451','1926.500','1926.501','1926.502','1926.503','1926.651','1926.652','1926.1053'],
    'machinery': ['1910.211','1910.212','1910.213','1910.215','1910.216','1910.217','1910.219','1910.242','1910.243','1910.244'],
    'energy': ['1910.147','1910.301','1910.302','1910.303','1910.304','1910.305','1910.306','1910.307','1910.308','1910.331','1910.332','1910.333','1910.334','1910.335','1926.416','1926.417'],
    'health': ['1910.94','1910.95','1910.97','1910.119','1910.120','1910.141','1910.146','1910.1000','1910.1200'],
    'emergency': ['1910.36','1910.37','1910.38','1910.39','1910.106','1910.151','1910.155','1910.156','1910.157','1910.158','1910.159','1910.160','1910.161','1910.162','1910.163','1910.164','1910.165'],
    'ppe': ['1910.132','1910.133','1910.134','1910.135','1910.136','1910.137','1910.138','1910.140','1926.28'],
    'applicability': ['1910.5','1910.6','1910.12','1904.7','1904.39','1926.20','1926.21'],
}
OWNERS = {section: owner for owner,sections in _GROUPS.items() for section in sections}

def owner_for(source):
    if isinstance(source,str):source=json.loads(source)
    explicit=source.get('specialist')
    if explicit in PROFILES:return explicit
    matches=re.findall(r'(?<!\d)(?:1910|1926|1904)\.\d+',source.get('clause') or source.get('section',''))
    if matches:return OWNERS.get(matches[0],'unmapped')
    if source.get('source_id') in ('usc-29-654','osha-fom-chapter-4','osha-state-plans'):return 'applicability'
    return 'unmapped'
