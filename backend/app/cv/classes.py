"""Explicit semantic groups; original detector classes are never renamed."""
DEFAULT_GROUPS={
    'vehicles':['car','truck','bus','motorcycle','bicycle','forklift','reach truck','pallet jack','telehandler','excavator'],
    'industrial_vehicles':['forklift','reach truck','pallet jack','telehandler','excavator'],
}
INDUSTRIAL_LABELS=['person',*DEFAULT_GROUPS['vehicles']]

def matches(label,targets,groups):
    return any(label in groups.get(target[6:],[]) if target.startswith('group:') else label==target for target in targets)

def groups_for(label,groups):
    return [name for name,members in groups.items() if label in members]

def filtered_detections(detections,labels,config):
    import numpy as np
    allowed=None if config.enabled_labels is None else set(config.enabled_labels)
    mask=np.array([label is not None and (allowed is None or label in allowed) and
                   float(score)>=config.class_confidence.get(label,config.confidence)
                   for id,score in zip(detections.class_id,detections.confidence)
                   for label in [labels.get(int(id))]],dtype=bool)
    return detections[mask]

def inference_floor(config):
    # A low per-class override must reach post-filtering instead of being discarded by the detector.
    return min([config.confidence,*config.class_confidence.values()])
