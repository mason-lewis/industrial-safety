import type {SceneReview,FramePacket,LabConfig,RuleCandidate,RiskAssessment,EvidenceBundle,ReviewFeedback,IncidentMarker,ResponseReport,AgentRunRecord,DetectorCapabilities} from './contracts';
export interface Source {id:string;name:string;kind:string}
export interface Session {camera_id?:string;session_id?:string;id?:string;source_id:string;run_generation:number;timestamp_ms?:number;duration_ms?:number|null;source_name?:string;source_kind?:string;speed?:number;health?:string;playing?:boolean;mode?:string}
export interface AIState {enabled:boolean;hosted_images_allowed:boolean;available?:boolean;reason?:string;model_id?:string;reasoning?:string;[key:string]:unknown}
export interface CVProfile{enabled_labels:string[];groups:Record<string,{configured:string[];available:string[];enabled:string[]}>;forklift:string;tracking:string;note:string}
export interface State {rule_coverage?:Record<string,unknown>[];segment_gaps?:Record<string,unknown>[];cv_profile?:CVProfile;detector_labels?:string[];scene_reviews?:SceneReview[];scene_events?:Record<string,unknown>[];health?:{state:string;message:string;stale?:boolean;overloaded?:boolean;suspected_frozen?:boolean;freeze_note?:string};session:Session|null;config:LabConfig;capabilities:DetectorCapabilities[];metrics:Record<string,unknown>;ai:AIState;events:RuleCandidate[];incidents:IncidentMarker[];agent_runs:AgentRunRecord[]}
export interface FrameEnvelope {type:'frame';frame:FramePacket;jpeg:string}
export interface EventDetail {candidate:RuleCandidate;assessments:RiskAssessment[];evidence:EvidenceBundle;feedback:ReviewFeedback[]}
export interface IncidentDetail {incident:IncidentMarker;report:ResponseReport|null;coverage:Record<string,unknown>}
