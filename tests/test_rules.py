import pytest
from app.models.contracts import FramePacket, LabConfig, RuleConfig, TrackObservation, ZoneConfig
from app.rules.engine import RuleEngine

CTX = dict(source_id="test", camera_id="cam", session_id="s1", run_generation=0)


def frame(ms, x=.5, bottom=.5, present=True, label="person"):
    tracks = [TrackObservation(**CTX, track_id=1, label=label, score=.9,
                               box=(x - .05, bottom - .1, x + .05, bottom), timestamp_ms=ms, frame_id=ms)] if present else []
    return FramePacket(**CTX, timestamp_ms=ms, frame_id=ms, width=100, height=100,
                       evidence_cutoff_ms=ms, tracks=tracks)


def zone_engine(duration=2000, clear=1000):
    return RuleEngine(LabConfig(zones=[ZoneConfig(id="z", label="Restricted", points=[(.2,.2),(.8,.2),(.8,.8),(.2,.8)])],
                               rules=[RuleConfig(id="dwell", kind="zone_dwell", zone_id="z", duration_ms=duration, clear_ms=clear)]))


def test_dwell_uses_irregular_source_pts_and_deduplicates():
    engine = zone_engine()
    for ms in [100, 137, 900, 1999, 2099]:
        assert engine.process(frame(ms)) == []
    [candidate] = engine.process(frame(2400))
    assert candidate.start_ms == 100
    assert candidate.timestamp_ms == 2400
    assert candidate.measurements["dwell_ms"] == 2300
    assert engine.process(frame(4500)) == []


def test_clearance_persistence_reentry_and_new_episode():
    engine = zone_engine(duration=100)
    engine.process(frame(0))
    [candidate] = engine.process(frame(100))
    assert engine.process(frame(200, x=.95))[0].state == "clearing"
    assert engine.process(frame(800))[0].state == "active"
    engine.process(frame(900, x=.95))
    assert engine.process(frame(1900, x=.95))[0].state == "closed"
    engine.process(frame(2000))
    [new] = engine.process(frame(2100))
    assert new.id != candidate.id
    assert new.episode == 2


def test_track_loss_does_not_prove_clearance_and_resets_untriggered_dwell():
    engine = zone_engine(duration=100)
    engine.process(frame(0))
    engine.process(frame(90, present=False))
    assert engine.process(frame(100)) == []
    [candidate] = engine.process(frame(200))
    [lost] = engine.process(frame(5000, present=False))
    assert lost.id == candidate.id
    assert lost.state == "active"
    assert "clearance unknown" in lost.limitations[0]
    assert engine.process(frame(7000, present=False)) == []


def test_timestamp_discontinuity_requires_generation_reset_and_nonperson_ignored():
    engine = zone_engine(duration=0)
    assert engine.process(frame(500, label="truck")) == []
    with pytest.raises(ValueError, match="discontinuity"):
        engine.process(frame(499))


def test_missing_track_interrupts_visible_clearance_timer():
    engine = zone_engine(duration=100)
    engine.process(frame(0))
    engine.process(frame(100))
    engine.process(frame(200, x=.95))
    engine.process(frame(800, present=False))
    assert not any(c.state == "closed" for c in engine.process(frame(1500, x=.95)))
    assert engine.process(frame(2500, x=.95))[0].state == "closed"


def line_engine(direction="positive"):
    return RuleEngine(LabConfig(rules=[RuleConfig(id="line", kind="line_crossing", line=[(.2,.5),(.8,.5)], direction=direction, clear_ms=1000)]))


def test_directional_line_deadband_jitter_and_cooldown():
    engine = line_engine()
    assert engine.process(frame(0, bottom=.4)) == []
    assert engine.process(frame(100, bottom=.499)) == []
    assert engine.process(frame(150, bottom=.501)) == []
    [candidate] = engine.process(frame(200, bottom=.6))
    assert candidate.measurements["direction"] == "positive"
    assert engine.process(frame(300, bottom=.4)) == []  # wrong direction
    assert engine.process(frame(400, bottom=.6)) == []  # same episode cooldown
    engine.process(frame(1300, bottom=.4))
    assert engine.process(frame(1400, bottom=.6))[0].episode == 2


def test_line_extension_outside_segment_does_not_cross():
    engine = line_engine()
    engine.process(frame(0, x=.95, bottom=.4))
    assert engine.process(frame(2000, x=.95, bottom=.6)) == []


def test_line_crossing_uses_trajectory_segment_intersection():
    config = LabConfig(rules=[RuleConfig(id='line',kind='line_crossing',line=[(.2,.5),(.8,.5)])])
    engine = RuleEngine(config)
    engine.process(frame(0,x=.1,bottom=.1))
    assert len(engine.process(frame(200,x=.95,bottom=.9))) == 1
    engine = RuleEngine(config)
    engine.process(frame(0,x=.99,bottom=.49))
    assert engine.process(frame(200,x=.7,bottom=.9)) == []


def test_confidence_save_preserves_active_episode_and_visible_clearance():
    engine=zone_engine(duration=100)
    engine.process(frame(0)); [c]=engine.process(frame(100))
    assert engine.reconfigure(engine.config.model_copy(update={'confidence':.6,'version':2}),150)==[]
    assert engine.process(frame(200,x=.95))[0].id==c.id
    [closed]=engine.process(frame(1200,x=.95))
    assert closed.id==c.id and closed.state=='closed'
    assert closed.closed_ms==1200 and closed.updated_ms==1200


def test_changed_rule_retires_episode_without_claiming_clearance():
    engine=zone_engine(duration=0)
    [c]=engine.process(frame(0))
    config=engine.config.model_copy(update={'rules':[]})
    [retired]=engine.reconfigure(config,500)
    assert retired.id==c.id and retired.state=='closed'
    assert retired.closed_ms==500 and 'clearance unknown' in retired.closure_reason
    assert engine.states=={} and engine.episodes=={}


def test_long_lost_track_expires_with_explicit_uncertainty():
    engine=zone_engine(duration=0)
    [c]=engine.process(frame(0))
    [retired]=engine.process(frame(60001,present=False))
    assert retired.id==c.id and 'clearance unknown' in retired.closure_reason
    assert not engine.states and not engine.episodes


def test_rule_state_cap_persists_retired_candidates():
    engine=zone_engine(duration=0);engine.max_states=2
    packet=frame(0)
    packet.tracks=[packet.tracks[0].model_copy(update={'track_id':i}) for i in range(4)]
    changed=engine.process(packet)
    assert len(engine.states)==2 and len(engine.episodes)==2
    assert len({c.id for c in changed})==4
    assert sum(c.state=='closed' for c in {c.id:c for c in changed}.values())==2


def multi_frame(ms, objects, width=200, height=100):
    packet=frame(ms,present=False);packet.width=width;packet.height=height
    packet.tracks=[TrackObservation(**CTX,track_id=id,label=label,score=.9,
        box=(x-.03,y-.08,x+.03,y),timestamp_ms=ms,frame_id=ms) for id,label,x,y in objects]
    return packet


def configured(kind, **changes):
    zone=ZoneConfig(id='z',label='Work cell',points=[(.2,.2),(.8,.2),(.8,.8),(.2,.8)])
    rule=RuleConfig(id='r',kind=kind,zone_id='z',duration_ms=100,clear_ms=100,**changes)
    return RuleEngine(LabConfig(zones=[zone],rules=[rule]))


def test_entry_requires_visible_outside_to_inside_and_rearms_after_cooldown():
    engine=configured('zone_entry')
    assert engine.process(frame(0))==[]  # Already inside is not observed entry.
    engine.process(frame(100,x=.95))
    [c]=engine.process(frame(200))
    assert c.state=='closed' and c.measurements['transition']=='outside to inside'
    assert engine.process(frame(250))==[]
    engine.process(frame(300,x=.95));[other]=engine.process(frame(400))
    assert other.id!=c.id and other.episode==2
    engine.process(frame(500,present=False));assert engine.process(frame(600))==[]


def test_class_selection_and_disabled_rules():
    engine=configured('zone_entry',target_labels=['truck'],label='Truck in pedestrian area')
    engine.process(frame(0,x=.95,label='truck'));[c]=engine.process(frame(100,label='truck'))
    assert c.track_ids==[1] and c.measurements['rule_label']=='Truck in pedestrian area'
    engine=configured('zone_dwell',enabled=False)
    assert engine.process(frame(0))==engine.process(frame(1000))==[]
    assert not engine.states


def test_untriggered_dwell_resets_on_visible_exit_even_before_clearance_delay():
    engine=configured('zone_dwell')
    engine.process(frame(0));engine.process(frame(90,x=.95));assert engine.process(frame(100))==[]
    assert engine.process(frame(190))==[]
    assert engine.process(frame(200))[0].start_ms==100


def test_occupancy_threshold_duration_loss_and_visible_clearance():
    engine=configured('zone_occupancy',max_count=1)
    people=[(1,'person',.3,.4),(2,'person',.6,.4)]
    assert engine.process(multi_frame(0,people))==[]
    [c]=engine.process(multi_frame(100,people))
    assert c.track_ids==[1,2] and c.measurements['count']==2
    assert engine.process(multi_frame(200,people))==[]
    [lost]=engine.process(multi_frame(300,people[:1]))
    assert lost.state=='active' and 'clearance unknown' in lost.limitations[0]
    exited=[people[0],(2,'person',.95,.4)]
    assert engine.process(multi_frame(400,exited))[0].state=='clearing'
    assert engine.process(multi_frame(500,exited))[0].state=='closed'


def test_occupancy_untriggered_timer_resets_when_count_drops():
    engine=configured('zone_occupancy',max_count=1)
    objects=[(1,'person',.3,.4),(2,'person',.6,.4)]
    engine.process(multi_frame(0,objects));engine.process(multi_frame(90,objects[:1]))
    assert engine.process(multi_frame(100,objects))==[]
    assert engine.process(multi_frame(200,objects))[0].start_ms==100


def test_proximity_uses_aspect_ratio_image_diagonal_and_both_classes():
    engine=configured('proximity',distance_threshold=.1,secondary_labels=['truck'])
    close=[(1,'person',.4,.5),(2,'truck',.5,.5)]
    engine.process(multi_frame(0,close));[c]=engine.process(multi_frame(100,close))
    assert c.track_ids==[1,2] and c.measurements['distance']==pytest.approx(20/(200**2+100**2)**.5)
    assert c.measurements['units']=='fraction of image diagonal'
    assert 'not physical distance' in c.limitations[-1]
    assert engine.process(multi_frame(200,close))==[]
    [lost]=engine.process(multi_frame(300,close[:1]));assert lost.state=='active'
    far=[close[0],(2,'truck',.75,.5)]
    assert engine.process(multi_frame(400,far))[0].state=='clearing'
    assert engine.process(multi_frame(500,far))[0].state=='closed'


def test_proximity_scope_pair_deduplication_and_track_loss_reset():
    engine=configured('proximity',distance_threshold=.1,target_labels=['person'],secondary_labels=['person'])
    objects=[(1,'person',.5,.5),(2,'person',.55,.5)]
    engine.process(multi_frame(0,objects));engine.process(multi_frame(90,objects[:1]))
    assert engine.process(multi_frame(100,objects))==[]
    assert len(engine.process(multi_frame(200,objects)))==1
    engine=configured('proximity',distance_threshold=.2,secondary_labels=['truck'])
    outside=[(1,'person',.81,.5),(2,'truck',.85,.5)]
    engine.process(multi_frame(0,outside));assert engine.process(multi_frame(1000,outside))==[]


def test_two_independent_rules_preserve_episode_and_rule_context():
    engine=configured('zone_dwell',label='Work cell',review_instruction='Check machine access')
    other=RuleConfig(id='door',kind='line_crossing',line=[(.2,.45),(.8,.45)])
    engine.config.rules.append(other)
    engine.process(frame(0,bottom=.4));changed=engine.process(frame(100,bottom=.6))
    assert {c.rule_id for c in changed}=={'r','door'}
    assert next(c for c in changed if c.rule_id=='r').measurements['rule_label']=='Work cell'


def test_vehicle_group_rule_matches_raw_forklift_without_renaming_or_matching_person():
    engine=configured('zone_entry',target_labels=['group:vehicles'])
    engine.process(frame(0,x=.95,label='forklift'))
    [c]=engine.process(frame(100,label='forklift'))
    assert c.measurements['subject']=='forklift'
    engine=configured('zone_entry',target_labels=['group:vehicles'])
    engine.process(frame(0,x=.95,label='person'));assert engine.process(frame(100,label='person'))==[]
