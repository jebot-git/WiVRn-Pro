#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Keep every model outcome in a compact, reproducible evidence table."""
import hashlib
import json
from pathlib import Path
import sys

source = Path(sys.argv[1]).read_bytes()
data = json.loads(source)
columns = ('pair_bytes', 'stripe_width', 'service_rate_mbps', 'scenario', 'burst',
           'position', 'layout', 'actual_erased_records', 'published',
           'modeled_first_record_to_stereo_ms', 'modeled_generation_to_stereo_ms',
           'scheduled_all_wire_service_ms', 'recovered_shards', 'receiver_highwater_bytes')
result = {key: value for key, value in data.items() if key != 'rows'}
result.update(full_result_sha256=hashlib.sha256(source).hexdigest(),
              columns=columns, rows=[[r[c] for c in columns] for r in data['rows']],
              sources={str(r['pair_bytes']): r['source_sha256'] for r in data['rows']},
              accounting={f"{r['pair_bytes']}/{r['stripe_width']}": r['accounting'] for r in data['rows']})
Path(sys.argv[2]).write_text(json.dumps(result, separators=(',', ':'))+'\n')
