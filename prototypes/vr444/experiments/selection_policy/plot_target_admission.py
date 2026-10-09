#!/usr/bin/env python3
"""Plot target-admission sensitivity; consumes the sealed offline result only."""
import argparse
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    data = json.loads(args.input.read_text())
    if data.get('status') != 'pass' or data.get('analysis_kind') != 'original_runtime_target_admission_sensitivity':
        raise ValueError('expected audited target-admission result')
    fig, ax = plt.subplots(figsize=(10.5, 6.4))
    colors = {None:'#505e70', 0:'#b06f31', 1:'#b13f53', 2:'#167d8d'}
    labels = {None:'No target ceiling', 0:'Target at or before runtime target',
              1:'Runtime target + one bounded-newest horizon', 2:'Runtime target + two horizons'}
    for bound in data['target_bounds']:
        rows = bound['best_tested_phase_per_advance']
        periods = bound['target_periods']
        ax.plot([r['publication_advance_ns']/1e6 for r in rows], [r['best_good_margin'] for r in rows],
                label=labels[periods], color=colors[periods], marker='o', markersize=6,
                linewidth=2.2, linestyle='--' if periods is None else '-')
    ax.axhline(0, color='#202a35', linewidth=1)
    ax.set(xlabel='Hypothetical publication advance, clipped at each eye’s receipt (ms)',
           ylabel='Fresh-frame count minus required count for p95 < 15 ms',
           xticks=[0,1,2,4,8], ylim=(-1700, 90))
    ax.grid(alpha=.2)
    ax.legend(loc='lower right', frameon=True)
    ax.annotate('One-horizon ceiling: −112 even at 4–8 ms advance', xy=(4,-112),
                xytext=(2.5,-450), arrowprops=dict(arrowstyle='->',color='#b13f53'), color='#8f2638')
    fig.suptitle('Farther-future pose admission is necessary for the tested conditional result',
                 fontsize=14, weight='bold', y=.98)
    ax.set_title('Best of 25 tested phases for each advance and ceiling; all counts 1,900…1,875 audited', fontsize=10, pad=13)
    fig.text(.075,.055,'500 offline cells • same immutable run05 • exact encoded-frame count frontier\n'
             'Unlimited known-pair retention; hypothetical timing. No runnable policy or measured headset improvement.',
             fontsize=9, color='#46515c')
    fig.subplots_adjust(left=.10,right=.97,bottom=.22,top=.84)
    fig.savefig(args.output, dpi=150)


if __name__ == '__main__':
    main()
