"""Exact, scoped aliases only. No fuzzy IDs, invented evidence or scope expansion."""


def register_frame(deps, frame, citation_alias=None):
    if frame.id not in deps.seen_frames:
        raise ValueError('Cannot register an alias for unseen evidence')
    for alias in (frame.frame_id, citation_alias):
        if alias:
            deps.frame_alias_targets.setdefault(alias,set()).add(frame.id)


def resolve_frame(deps, reference):
    value=reference.strip()
    if value in deps.seen_frames:return value
    targets=deps.frame_alias_targets.get(value,set())
    if len(targets)==1:
        target=next(iter(targets))
        if target in deps.seen_frames:return target
    return value


def normalize_video_references(deps,value):
    changes=[]
    seen=set()
    def visit(record):
        if isinstance(record,list):
            for item in record:visit(item)
            return
        if id(record) in seen:return
        fields=getattr(type(record),'model_fields',None)
        if fields is None:
            # Compatibility with small coordinator/test result adapters.
            fields={key:None for key in ('observations','screening_checks','answer','claims','evidence_refs') if hasattr(record,key)}
        if not fields:return
        seen.add(id(record))
        for name in fields:
            current=getattr(record,name)
            if name in ('evidence_refs','supporting_refs','contradicting_refs'):
                refs=[]
                for original in current:
                    canonical=resolve_frame(deps,original)
                    if canonical!=original:changes.append({'supplied':original,'canonical':canonical})
                    if canonical not in refs:refs.append(canonical)
                setattr(record,name,refs)
            else:visit(current)
    visit(value)
    return changes


def permitted_aliases(deps):
    return {alias:next(iter(targets)) for alias,targets in sorted(deps.frame_alias_targets.items())
            if len(targets)==1 and next(iter(targets)) in deps.seen_frames}
