#!/usr/bin/env python3
"""Export a capture-only scientific plot; complete matrices stay in the receipt."""
import argparse
import hashlib
import json
import statistics
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from audit import mapped


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('receipt', type=Path)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('output_prefix', type=Path)
    args = parser.parse_args()
    receipt = json.loads(args.receipt.read_text())
    if (receipt.get('experiment') != 'mapped-input-consumer' or receipt.get('headset_used') is not False
            or receipt.get('promotion_blocked') is not True):
        raise ValueError('qualified host-only consumer receipt required')
    groups = mapped.rrf.groups(json.loads(args.manifest.read_text()))
    captured = {name: int(row['eyes'][0]['q']) for name, row in groups.items() if not row['synthetic']}
    if len(captured) != 12 or set(captured.values()) != {1, 8}:
        raise ValueError('complete captured-pair source manifest required')
    devices = receipt['captured_joint_paired_comparisons']
    if len(devices) != 2 or len({tuple(row['device']) for row in devices}) != 2:
        raise ValueError('two distinct host device summaries required')
    for report in receipt['measurements']:
        if digest(args.manifest) != report['conditions']['manifest_sha256'][0]:
            raise ValueError('plot/source manifest identity mismatch')
    names = sorted(captured)
    specifications = [(kind, memory) for kind in ('rrf', 'rrb32') for memory in ('default', 'cached')]
    labels = ['RRF\ndefault', 'RRF\ncached', 'RRB32\ndefault', 'RRB32\ncached']
    colors = {1: '#c66b00', 8: '#176ca4'}
    fig, axes = plt.subplots(2, 2, figsize=(12.2, 8.7), sharex=True, sharey=True)
    dots = []
    for device_index, device in enumerate(devices):
        comparisons = {(item['baseline'], item['candidate']): item for item in device['comparisons']}
        short = 'Arc A770' if device['device'][1] == 2 else 'UHD 770'
        for column, metric in enumerate(('p50_ms', 'p95_ms')):
            axis = axes[device_index, column]
            axis.axhline(0, color='#444444', linewidth=1.1)
            axis.grid(axis='y', alpha=.18)
            for position, (kind, memory) in enumerate(specifications):
                pair = (f'{kind}_staged_{memory}', f'{kind}_direct_{memory}')
                values = comparisons[pair]['paired_differences'][metric]['per_pair_candidate_minus_baseline_ms']
                if set(values) != set(captured):
                    raise ValueError('plot cannot omit a captured pair')
                for index, name in enumerate(names):
                    jitter = (index - (len(names)-1)/2) * .036
                    value = values[name]
                    axis.scatter(position+jitter, value, c=colors[captured[name]], s=27,
                                 linewidths=.25, edgecolors='white', alpha=.88, zorder=3)
                    dots.append(dict(device=device['device'], metric=metric, baseline=pair[0],
                                     candidate=pair[1], captured_pair=name, q=captured[name], delta_ms=value))
                axis.scatter(position, statistics.median(values.values()), marker='D', color='#111111',
                             s=42, edgecolors='white', linewidths=.6, zorder=4)
            axis.set_title(f'{short} · {"median" if column == 0 else "p95"} per-pair difference', fontsize=12, pad=11)
            axis.set_xticks(range(4), labels, fontsize=10)
            axis.set_xlim(-.5, 3.5)
            axis.spines[['top', 'right']].set_visible(False)
            if column == 0:
                axis.set_ylabel('Direct minus staged (ms)\nNegative = faster', fontsize=10)
    handles = [Line2D([], [], marker='o', linestyle='', color=colors[1], label='Q1 captured pair (4)'),
               Line2D([], [], marker='o', linestyle='', color=colors[8], label='Q8 captured pair (8)'),
               Line2D([], [], marker='D', linestyle='', color='#111111', label='Median of 12 paired differences')]
    fig.suptitle('Direct mapped output through the first stereo image consumer', fontsize=16, y=.98)
    fig.text(.5, .934, 'Host-only matched comparisons · 31 repetitions per profile · individual tail regressions retained',
             ha='center', fontsize=11)
    fig.legend(handles=handles, loc='upper center', bbox_to_anchor=(.5, .914), ncol=3, frameon=False, fontsize=10)
    fig.text(.07, .055,
             'Arc default/cached labels select the same memory type. UHD uses distinct queried types.\n'
             'Each comparison repeats the same 12 captured pairs; groups are not independent datasets. All 14 synthetic profiles remain in the receipt.\n'
             'Private three-submit path includes image poison; this is not native compositor or headset latency.',
             fontsize=9, linespacing=1.45)
    fig.subplots_adjust(top=.845, bottom=.18, left=.085, right=.98, hspace=.32, wspace=.12)
    svg = args.output_prefix.with_suffix('.svg')
    png = args.output_prefix.with_suffix('.png')
    for path in (svg, png, args.output_prefix.with_suffix('.json')):
        if path.exists():
            raise ValueError('plot output must be new')
    fig.savefig(svg, metadata={'Date': '2026-10-09', 'Creator': 'Matplotlib host-only GPU research'})
    fig.savefig(png, dpi=150)
    plt.close(fig)
    root = Path(__file__).resolve().parents[4]
    relative = lambda path: str(path.resolve().relative_to(root))
    metadata = dict(schema=1, headset_used=False, promotion_eligible=False,
                    receipt=dict(path=relative(args.receipt), sha256=digest(args.receipt)),
                    manifest=dict(path=relative(args.manifest), sha256=digest(args.manifest)),
                    source_sha256={relative(Path(__file__)): digest(Path(__file__))},
                    images=[dict(path=relative(path), sha256=digest(path)) for path in (svg, png)],
                    points=dots, point_count=len(dots),
                    scope='Captured-pair figure only; all 26 profiles remain in the linked numeric receipt.')
    args.output_prefix.with_suffix('.json').write_text(json.dumps(metadata, indent=2, allow_nan=False)+'\n')


if __name__ == '__main__':
    main()
