"""Independent schema-2 bounded-selection replay; never a counterfactual speedup."""

FRAME_FIELDS = ('present', 'frame', 'warp', 'view_display_time_xr_ns', 'receive_xr_ns', 'ready_xr_ns', 'alpha')


def same(a, b, strict=False):
    return (a['present'] and b['present'] and a['frame'] == b['frame'] and a['warp'] == b['warp']
            and (not strict or (a['view_display_time_xr_ns'] == b['view_display_time_xr_ns'] and a['alpha'] == b['alpha'])))


def snapshot_equal(a, b):
    return all(all(x.get(k) == y.get(k) for k in FRAME_FIELDS) for x, y in zip(a, b))


def coherent(pair):
    return (same(pair[0], pair[1], True) and pair[0]['view_display_time_xr_ns'] > 0
            and (same(pair[0], pair[2], True) if pair[0]['alpha'] else not pair[2]['present']))


def replay(record):
    history, previous = record['history'], record['previous']
    target, period, prior = (record[k] for k in ('predicted_display_xr_ns', 'predicted_display_period_xr_ns', 'previous_predicted_display_xr_ns'))
    for value in (period, prior):
        if type(value) is not int or not 0 <= value < 2**63:
            raise ValueError('invalid selection period/prior target')
    policy = record['selection_policy']
    if policy not in ('closest-target', 'bounded-newest'):
        raise ValueError('unknown selection policy')
    bound = min(period, target-prior) if period > 0 and 0 < prior < target else 0
    if type(record.get('max_ahead_xr_ns')) is not int or record['max_ahead_xr_ns'] != bound:
        raise ValueError('future-target bound differs from runtime period evidence')
    alpha = bool(history[0]['present'] and history[0]['alpha'])
    empty = [dict(decoder=eye, present=False) for eye in range(3)]

    def slots_for(slot, strict):
        left = history[slot]
        if not left['present'] or (strict and (left['alpha'] != alpha or left['view_display_time_xr_ns'] <= 0)):
            return None
        slots = [slot, -1, -1]
        for eye in range(1, 2+int(alpha)):
            matches = [j for j in range(3) if same(left, history[eye*3+j], strict)]
            if not matches:
                return None
            slots[eye] = matches[0]
        return slots

    def snapshot(slots):
        return [history[eye*3+slot] if slot >= 0 else empty[eye] for eye, slot in enumerate(slots)]

    def forward(candidate, reference):
        if not reference['present']:
            return True
        if candidate['frame'] < reference['frame'] or candidate['view_display_time_xr_ns'] < reference['view_display_time_xr_ns']:
            return False
        return candidate['frame'] != reference['frame'] or same(candidate, reference, True)

    legacy = [(slot, slots_for(slot, False)) for slot in range(3)]
    legacy = [(slot, slots) for slot, slots in legacy if slots is not None]
    closest_slots = min(legacy, key=lambda item: abs(history[item[0]]['view_display_time_xr_ns']-target))[1] if legacy else [-1]*3
    if legacy:
        # Legacy resource lookup uses first frame ID, after candidate matching
        # used frame+warp. Keep the default behavior observable even for malformed
        # duplicate-ID histories; later identity validation still rejects them.
        frame = history[closest_slots[0]]['frame']
        closest_slots = [next((j for j in range(3) if history[eye*3+j]['present'] and history[eye*3+j]['frame'] == frame), -1)
                         if eye < 2+int(alpha) else -1 for eye in range(3)]
    closest = snapshot(closest_slots)
    if not snapshot_equal(record['closest'], closest):
        raise ValueError('closest candidate snapshot differs from independent replay')
    eligible = [False]*3
    chosen_slots = [-1]*3
    chosen = None
    if policy == 'closest-target' and legacy:
        chosen_slots, chosen = closest_slots, closest
        for slot, _ in legacy:
            eligible[slot] = True
        reason = 'closest_target'
    elif policy == 'bounded-newest' and legacy:
        for slot in range(3):
            slots = slots_for(slot, True)
            candidate = history[slot]
            if slots is None or not forward(candidate, closest[0]) or (coherent(previous) and not forward(candidate, previous[0])):
                continue
            if candidate['view_display_time_xr_ns'] > target+bound or (bound == 0 and not same(candidate, closest[0], True)):
                continue
            eligible[slot] = True
            if chosen is None or candidate['frame'] > chosen[0]['frame']:
                chosen_slots, chosen = slots, snapshot(slots)
        reason = 'bounded_newest'
    else:
        reason = 'no_complete_fallback'
    fallback = chosen is None
    if fallback:
        reason = 'no_complete_fallback' if not legacy else 'no_eligible_fallback'
        chosen = previous if policy == 'closest-target' or coherent(previous) else empty
    if record.get('reason') != reason:
        raise ValueError('selection reason differs from independent replay')
    flags = record.get('eligible_left_slots')
    slots = record.get('chosen_slots')
    if type(flags) is not list or len(flags) != 3 or any(type(x) is not bool for x in flags) or flags != eligible:
        raise ValueError('bounded eligible slots differ from independent replay')
    if type(slots) is not list or len(slots) != 3 or any(type(x) is not int or not -1 <= x <= 2 for x in slots) or slots != chosen_slots:
        raise ValueError('chosen slots differ from independent replay')
    if not snapshot_equal(record['selected'], chosen):
        raise ValueError('selected frame metadata differs from independent replay')
    actual_alpha = bool(chosen[0]['present'] and chosen[0]['alpha'])
    if type(record.get('selected_alpha_required')) is not bool or record['selected_alpha_required'] != actual_alpha:
        raise ValueError('selected alpha participation mismatch')
    return dict(fallback=fallback, closest=closest, chosen=chosen, eligible=eligible, max_ahead_xr_ns=bound)
