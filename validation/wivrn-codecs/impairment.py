"""Seeded packet scheduling oracle; does NOT modify qdiscs or live networking.

Rate is application datagram service rate, not Wi-Fi PHY rate. A deployment
adapter must implement/measure this model or label its different mechanism.
"""
import random


def schedule(packets, profile, seed=444, queue_ms=20):
    rng = random.Random(seed)
    link_free = 0.0
    deliveries, drops = [], []
    rate = profile["goodput_mbps"]
    if rate <= 0 or queue_ms < 0:
        raise ValueError("invalid service rate/queue")
    previous = -1
    for packet in packets:
        pid, sent, size, deadline = (packet[k] for k in ("id", "sent_ns", "bytes", "deadline_ns"))
        if sent < previous or size <= 0 or deadline <= sent:
            raise ValueError("packets must be chronological, nonempty and have a future deadline")
        previous = sent
        start = max(sent, link_free)
        if start - sent > queue_ms * 1e6:
            drops.append(dict(id=pid, reason="queue"))
            continue
        link_free = start + size * 8000 / rate
        period = profile.get("burst_period_ms", 0)
        burst = period and (sent / 1e6) % period < profile.get("burst_duration_ms", 0)
        if burst or rng.random() < profile.get("loss", 0):
            drops.append(dict(id=pid, reason="loss"))
            continue
        arrival = link_free + max(0, profile.get("delay_ms", 0) + rng.uniform(-1, 1) * profile.get("jitter_ms", 0)) * 1e6
        if rng.random() < .1:
            arrival += profile.get("reorder_ms", 0) * 1e6
        if arrival >= deadline:
            drops.append(dict(id=pid, reason="deadline"))
            continue
        deliveries.append(dict(id=pid, arrival_ns=int(arrival), duplicate=False))
        if rng.random() < profile.get("duplicate", 0):
            duplicate_time = int(arrival + rng.uniform(0, 100000))
            if duplicate_time < deadline:
                deliveries.append(dict(id=pid, arrival_ns=duplicate_time, duplicate=True))
    return dict(deliveries=sorted(deliveries, key=lambda p: p["arrival_ns"]), drops=drops)
