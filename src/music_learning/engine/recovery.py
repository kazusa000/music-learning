"""Recover owned audio processes through the same public SDK operations."""


def recover(client):
    previous = client.call('engine.status')
    client.call('engine.close')
    if not previous.get('project_id'):
        return client.call('engine.status')
    state = client.call('engine.open', dict(project_id=previous['project_id'],
        revision_id=previous['revision_id'], view=previous['view'],
        position_sample=previous.get('position_sample', 0), owner_id=previous.get('owner_id') or None,
        playing=False))
    identity = dict(engine_session=state['engine_session'], generation=state['generation'],
        owner_id=previous.get('owner_id') or None)
    tracks = {key: {field: value for field, value in values.items() if field in ('gain_db', 'pan', 'mute', 'solo')}
        for key, values in previous.get('monitor', {}).items() if key in state.get('monitor', {})}
    if tracks:
        state = client.call('engine.monitor', dict(**identity, tracks=tracks))
    if previous.get('loop'):
        state = client.call('engine.transport', dict(**identity, playing=False, loop=True,
            loop_start_sample=previous['loop_start_sample'], loop_end_sample=previous['loop_end_sample']))
    return state
