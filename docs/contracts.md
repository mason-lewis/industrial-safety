# Shared integration contract

All API paths use `/api`. Backend at localhost:8000, Vite proxy at localhost:5173.
Backend owns Pydantic schemas in `backend/app/models/contracts.py`; lead generates TypeScript.

- GET /sources -> [{id,name,kind}]; POST /sources/refresh -> same
- POST /sessions {source_id,mode:live_simulation|offline} -> session
- GET /state -> {session,config,capabilities,metrics,ai,events,incidents,agent_runs}
- POST /playback {action:play|pause|step|seek|restart,speed?,timestamp_ms?}
- PUT /config {zones?,rules?,confidence?,target_fps?,scene_review_enabled?,scene_review_interval_s?}
- POST /ai {enabled,hosted_images_allowed} -> ai state
- POST /events/{id}/review; GET /events/{id} -> {candidate,assessments,evidence,feedback}
- POST /events/{id}/feedback {disposition:accepted|rejected|uncertain,note?}
- POST /incidents {timestamp_ms,pre_ms,post_ms} -> incident
- POST /incidents/{id}/review; GET /incidents/{id} -> {incident,report,coverage}
- GET /evidence/{id}/image -> JPEG; protected ID lookup only
- GET /history -> persisted records
- WebSocket /ws -> {type:frame,frame:FramePacket,jpeg:base64} or {type:state,...state}

FramePacket: source_id,camera_id,session_id,run_generation,schema_version,frame_id,timestamp_ms,width,height,tracks: TrackObservation[], processing_ms,config_version,evidence_cutoff_ms. TrackObservation: same context,track_id,label,score,box:[x1,y1,x2,y2] normalized, trail:[[x,y]],timestamp_ms,frame_id.
ZoneConfig: id,label,points:[[x,y]],version. RuleConfig: id,kind:zone_dwell|line_crossing,zone_id?,duration_ms,clear_ms,line?:[[x,y],[x,y]],direction:positive|negative|both,version.
RuleCandidate: same context,id,kind,rule_id,zone_ids,track_ids,timestamp_ms,start_ms,evidence_cutoff_ms,episode,state:candidate|active|clearing|closed,ai_status:disabled|queued|running|reviewed|timeout|error|skipped,measurements,limitations,config_version,processing_time.
RiskAssessment: same context,candidate_id,review_mode,evidence_cutoff_ms,assessment,severity,risk_type,explanation,observed_facts,inferred_risks,alternative_explanations,evidence_refs,limitations,missing_evidence,recommended_human_review,model_id,prompt_version,token_usage,tool_usage,elapsed_ms,uncalibrated_confidence.
EvidenceRef: same context,id,frame_id,timestamp_ms,kind:original|crop|annotated,config_version,processing_time.
Metrics fields may be a extensible dictionary; missing measurements are null and cost is unknown by default.

Ownership: backend agent owns pipeline/source/detector/rules/evidence/storage/API main plus contracts.py. Frontend agent owns frontend/. Evaluation agent owns tests, data/evals and docs evaluation fixtures. Lead owns agents, configuration, Python packaging, contract generation, integration docs. Avoid editing another owner's files without coordination.
