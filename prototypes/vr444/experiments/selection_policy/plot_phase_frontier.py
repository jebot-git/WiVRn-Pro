#!/usr/bin/env python3
"""Render the finite conditional phase grid as a standalone research figure."""
import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise ValueError('figure output already exists')
    result = json.loads(args.input.read_text())
    if result.get('status') != 'pass' or result.get('analysis_kind') != 'finite_positive_phase_exact_count_grid':
        raise ValueError('figure requires completed phase-grid analysis')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.colors import BoundaryNorm, ListedColormap
    from matplotlib.patches import Rectangle
    phases, advances = result['phase_grid_ns'], result['publication_advance_grid_ns']
    cells = {(row['phase_ns'], row['publication_advance_ns']): row for row in result['grid_summary']}
    if len(cells) != len(phases) * len(advances) or any(not row['feasible'] for row in cells.values()):
        raise ValueError('figure requires the exact complete feasible grid')
    values = [[cells[phase, advance]['best_good_margin'] for advance in advances] for phase in phases]
    colors = ['#a83240', '#c45457', '#db7770', '#e99e8a', '#f4c7ad', '#d6e8ba', '#b0d899', '#83c67f', '#59ac69', '#368d54']
    bounds = [-1901, -500, -100, -50, -20, 0, 1, 10, 20, 50, 1901]
    fig, ax = plt.subplots(figsize=(7.8, 11.8))
    fig.subplots_adjust(bottom=.17, top=.92, left=.14, right=.84)
    chart = ax.imshow(values, cmap=ListedColormap(colors), norm=BoundaryNorm(bounds, len(colors)), aspect='auto')
    ax.set_xticks(range(len(advances)), [f'{advance / 1e6:g}' for advance in advances])
    ax.set_yticks(range(len(phases)), [f'{phase / 1e6:.3f}' for phase in phases])
    ax.set_xlabel('Hypothetical publication advance per eye (ms; clipped at receipt)')
    ax.set_ylabel('Hypothetical later sampling and submission (ms)')
    ax.set_title('Known-supply phase sensitivity\nBest good-count surplus across 1,875–1,900 choices', loc='left', fontsize=13, weight='bold', pad=14)
    for y, phase in enumerate(phases):
        for x, advance in enumerate(advances):
            cell = cells[phase, advance]
            margin = cell['best_good_margin']
            ax.text(x, y, f'{margin:+d}', ha='center', va='center', fontsize=9,
                color='white' if margin < -500 or margin >= 50 else '#202020')
            if cell['highest_conditionally_qualifying_count'] is not None:
                ax.add_patch(Rectangle((x-.48, y-.48), .96, .96, fill=False, edgecolor='#194f2c', linewidth=2))
    colorbar = fig.colorbar(chart, ax=ax, fraction=.045, pad=.025, ticks=[-500, -100, -50, -20, 0, 1, 10, 20, 50])
    colorbar.set_label('Under-15ms count minus count required for p95')
    fig.text(.08, .035, 'Frozen run05 counterfactual; all future-target ceilings removed.\n'
        'Actual pre-warm identity preserved; unlimited known-pair retention and future observations allowed.\n'
        'Outlined cells have a conditional certificate. No executable-policy, live latency or optical claim.', fontsize=9)
    fig.savefig(args.out, dpi=150, bbox_inches='tight', facecolor='white')
    plt.close(fig)


if __name__ == '__main__':
    main()
