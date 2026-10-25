"""Deterministic image-plane episodes; track loss never proves visible clearance."""
from datetime import datetime, timezone
from math import hypot
from uuid import uuid4
import cv2
import numpy as np
from app.models.contracts import RuleCandidate
from app.cv.classes import matches

class RuleEngine:
    def __init__(self, config):
        self.config = config
        self.states = {}
        self.episodes = {}
        self.last_ms = None
        self.episode_sequence = 0
        self.max_states = 2048

    @staticmethod
    def anchor(track):
        return ((track.box[0]+track.box[2])/2, track.box[3])

    def state(self, key, timestamp):
        s = self.states.setdefault(key, dict(start=None, outside=None, side=None, point=None,
                                            inside=None, candidate=None))
        s['last_seen'] = timestamp
        return s

    def inside(self, track, polygon, rule=None, state=None):
        if polygon is None: return False
        distance = cv2.pointPolygonTest(polygon, self.anchor(track), True)
        tolerance = rule.tolerance if rule else 0
        # Schmitt trigger: enter beyond the inner margin; exit beyond the outer margin.
        previous = state.get('zone_inside') if state else None
        inside = distance >= (-tolerance if previous else tolerance)
        if state is not None: state['zone_inside'] = inside
        return inside

    def episode(self, frame, rule, ids, key, s, condition, measurements, changed, clear_observed=True):
        if condition:
            s['outside'] = None
            if s['start'] is None: s['start'] = frame.timestamp_ms
            c = s['candidate']
            if c and c.state == 'clearing':
                c.state = 'active'; changed.append(c)
            if c is None and frame.timestamp_ms-s['start'] >= rule.duration_ms:
                c = self._candidate(frame, rule, ids, s['start'], key)
                c.measurements |= measurements | {'dwell_ms':frame.timestamp_ms-s['start']}
                s['candidate'] = c; changed.append(c)
        elif clear_observed:
            if s['outside'] is None: s['outside'] = frame.timestamp_ms
            c = s['candidate']
            if c is None: s['start'] = None
            if c and c.state == 'active':
                c.state = 'clearing'; changed.append(c)
            if frame.timestamp_ms-s['outside'] >= rule.clear_ms:
                if c:
                    c.state = 'closed'; changed.append(c)
                s['candidate'] = None; s['start'] = None
        else:
            self.missing(s, changed)

    def missing(self, s, changed):
        s['outside'] = None; s['side'] = None; s['point'] = None; s['inside'] = None; s['zone_inside'] = None
        if s['candidate'] is None:
            if s['start'] is not None:
                s['interrupted_start'] = s['start']
            s['start'] = None
        elif 'Track not visible; clearance unknown' not in s['candidate'].limitations:
            s['candidate'].limitations.insert(0,'Track not visible; clearance unknown')
            changed.append(s['candidate'])

    def process(self, frame):
        if self.last_ms is not None and frame.timestamp_ms < self.last_ms:
            raise ValueError('Source timestamp discontinuity requires a new generation')
        self.last_ms = frame.timestamp_ms
        changed = []; seen = set()
        polygons = {z.id:np.array(z.points,np.float32) for z in self.config.zones}
        visible = {t.track_id for t in frame.tracks}
        for rule in self.config.rules:
            if not rule.enabled: continue
            targets = [t for t in frame.tracks if matches(t.label,rule.target_labels,self.config.class_groups)]
            polygon = polygons.get(rule.zone_id)
            if rule.kind == 'zone_occupancy':
                key = (rule.id, 'group'); seen.add(key); s = self.state(key, frame.timestamp_ms)
                members=s.setdefault('members',{})
                inside = [t.track_id for t in targets if self.inside(t, polygon,rule,members.setdefault(t.track_id,{}))]
                s['members']={id:state for id,state in members.items() if id in visible}
                prior = s['candidate'].track_ids if s['candidate'] else []
                self.episode(frame, rule, inside, key, s, len(inside)>rule.max_count,
                             dict(count=len(inside),max_count=rule.max_count,units='visible tracked objects'), changed,
                             clear_observed=set(prior).issubset(visible))
                continue
            if rule.kind == 'proximity':
                others = [t for t in frame.tracks if matches(t.label,rule.secondary_labels,self.config.class_groups)]
                for target in targets:
                    for other in others:
                        if target.track_id == other.track_id: continue
                        # When classes overlap, the same unordered pair still has one episode.
                        ids = sorted([target.track_id, other.track_id]); key = (rule.id, *ids)
                        if key in seen: continue
                        seen.add(key); s = self.state(key, frame.timestamp_ms)
                        a,b = self.anchor(target),self.anchor(other)
                        distance = hypot((a[0]-b[0])*frame.width, (a[1]-b[1])*frame.height)/hypot(frame.width,frame.height)
                        in_scope = rule.zone_id is None or (self.inside(target,polygon,rule,s.setdefault('target_zone',{})) and self.inside(other,polygon,rule,s.setdefault('other_zone',{})))
                        self.episode(frame,rule,ids,key,s,in_scope and distance<=rule.distance_threshold,
                                     dict(distance=distance,threshold=rule.distance_threshold,units='fraction of image diagonal',
                                          subjects=[target.label,other.label],anchor='bounding box bottom center'),changed)
                continue
            for track in targets:
                point = self.anchor(track); key = (rule.id,track.track_id)
                seen.add(key); s = self.state(key, frame.timestamp_ms)
                if rule.kind == 'zone_dwell':
                    self.episode(frame,rule,[track.track_id],key,s,self.inside(track,polygon,rule,s),
                                 dict(units='source milliseconds',anchor='bounding box bottom center',subject=track.label),changed)
                elif rule.kind == 'zone_entry':
                    inside = self.inside(track,polygon,rule,s)
                    if inside and s['inside'] is False and (s['outside'] is None or frame.timestamp_ms-s['outside']>=rule.clear_ms):
                        c = self._candidate(frame,rule,[track.track_id],frame.timestamp_ms,key)
                        c.state = 'closed'; c.measurements |= dict(transition='outside to inside',subject=track.label,anchor='bounding box bottom center')
                        changed.append(c); s['outside'] = frame.timestamp_ms
                    s['inside'] = inside
                elif rule.kind == 'line_crossing':
                    a,b = rule.line
                    cross = (b[0]-a[0])*(point[1]-a[1])-(b[1]-a[1])*(point[0]-a[0])
                    distance = cross / hypot(b[0]-a[0], b[1]-a[1])
                    side = 1 if distance > rule.tolerance else -1 if distance < -rule.tolerance else 0
                    previous = s['side']
                    if side and previous and side != previous:
                        direction = 'positive' if side>previous else 'negative'
                        delta = (b[0]-a[0],b[1]-a[1]); norm = delta[0]**2+delta[1]**2
                        old = s['point']; old_cross = delta[0]*(old[1]-a[1])-delta[1]*(old[0]-a[0])
                        fraction = old_cross/(old_cross-cross)
                        intersection = (old[0]+fraction*(point[0]-old[0]),old[1]+fraction*(point[1]-old[1]))
                        along = ((intersection[0]-a[0])*delta[0]+(intersection[1]-a[1])*delta[1])/norm
                        if 0<=along<=1 and rule.direction in ('both',direction) and (s['outside'] is None or frame.timestamp_ms-s['outside']>=rule.clear_ms):
                            c = self._candidate(frame,rule,[track.track_id],frame.timestamp_ms,key)
                            c.state = 'closed'; c.measurements |= dict(direction=direction,units='normalized image plane',subject=track.label)
                            changed.append(c); s['outside'] = frame.timestamp_ms
                    if side: s['side'] = side; s['point'] = point
        for key,s in self.states.items():
            if key not in seen: self.missing(s,changed)
        for key in list(self.states):
            s = self.states[key]
            if key not in seen and frame.timestamp_ms-s.get('last_seen',frame.timestamp_ms)>60000:
                self._retire(key,frame.timestamp_ms,'Track expired; clearance unknown',changed)
        while len(self.states)>self.max_states:
            key = min(self.states,key=lambda k:self.states[k].get('last_seen',0))
            self._retire(key,frame.timestamp_ms,'State retention limit reached; clearance unknown',changed)
        for c in changed:
            c.updated_ms = frame.timestamp_ms
            if c.state == 'closed' and c.closed_ms is None:
                c.closed_ms = frame.timestamp_ms
                c.closure_reason = c.closure_reason or ('Transition recorded' if c.kind in ('line_crossing','zone_entry') else 'Visible rule clearance')
        return list({c.id:c for c in changed}.values())

    def _retire(self,key,timestamp_ms,reason,changed):
        s = self.states.pop(key); self.episodes.pop(key,None)
        if s['candidate']:
            c = s['candidate']; c.state = 'closed'; c.updated_ms = timestamp_ms; c.closed_ms = timestamp_ms
            c.closure_reason = reason; c.limitations.append(reason); changed.append(c)

    def reconfigure(self,config,timestamp_ms):
        old_rules = {r.id:r for r in self.config.rules}; new_rules = {r.id:r for r in config.rules}
        old_zones = {z.id:z for z in self.config.zones}; new_zones = {z.id:z for z in config.zones}
        changed = []
        for key in list(self.states):
            old,new = old_rules.get(key[0]),new_rules.get(key[0])
            if old!=new or (old and old.zone_id and old_zones.get(old.zone_id)!=new_zones.get(old.zone_id)):
                self._retire(key,timestamp_ms,'Rule configuration changed; clearance unknown',changed)
        self.config = config
        return changed

    def _candidate(self,frame,rule,ids,start,key):
        self.episode_sequence += 1; self.episodes[key] = self.episode_sequence
        limitations = ['Rule trigger is an observation for review, not proof of a hazard or authorization breach.']
        if rule.kind == 'proximity': limitations.append('Image-plane proximity is not physical distance; perspective and occlusion can mislead.')
        return RuleCandidate(**{k:getattr(frame,k) for k in ('source_id','camera_id','session_id','run_generation','schema_version')},
            id=str(uuid4()),kind=rule.kind,rule_id=rule.id,zone_ids=[rule.zone_id] if rule.zone_id else [],track_ids=ids,
            timestamp_ms=frame.timestamp_ms,start_ms=start,evidence_cutoff_ms=frame.timestamp_ms,
            episode=self.episodes[key],state='active',config_version=frame.config_version,
            measurements={'rule_label':rule.label or rule.id,'rule_definition':rule.model_dump(mode='json'),
                          'detector_confidence':{str(t.track_id):t.score for t in frame.tracks if t.track_id in ids},
                          'identity_scope':'session-local track IDs; new IDs do not prove the same person',
                          'interrupted_start_ms':self.states.get(key,{}).get('interrupted_start'),
                          'zone_definition':next((z.model_dump(mode='json') for z in self.config.zones if z.id==rule.zone_id),None)},limitations=limitations + (['Prior dwell interrupted by track loss; new ID or reacquisition starts a new observed interval.'] if self.states.get(key,{}).get('interrupted_start') is not None else []),
            processing_time=datetime.now(timezone.utc).isoformat())
