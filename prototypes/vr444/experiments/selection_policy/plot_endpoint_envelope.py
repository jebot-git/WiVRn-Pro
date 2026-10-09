#!/usr/bin/env python3
"""Render finite-phase necessary endpoint bounds, never a feasibility heatmap."""
import argparse
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle,Patch


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('input',type=Path);parser.add_argument('--out',type=Path,required=True);args=parser.parse_args()
    r=json.loads(args.input.read_text());phases=[0,4_387_188,5_488_282,5_555_555,11_111_111]
    models=['encoded_one_horizon','encoded_unbounded','source_two_horizons','source_unbounded']
    if r.get('status')!='pass' or r.get('analysis_kind')!='finite_phase_all_endpoint_necessary_count_envelope' or r.get('phase_grid_ns')!=phases:
        raise ValueError('qualified endpoint envelope required')
    cells={(v['model'],v['phase_ns']):v for v in r['alternatives']}
    if len(cells)!=20 or set(cells)!={(m,p) for m in models for p in phases}:raise ValueError('complete exact endpoint grid required')
    fig,ax=plt.subplots(figsize=(12,5.7));fig.subplots_adjust(left=.21,right=.98,top=.77,bottom=.25)
    colors=['#e6e9ee','#f7c9be']
    for i,model in enumerate(models):
        for j,phase in enumerate(phases):
            row=cells[model,phase];margin=row['best_necessary_margin_interval']['necessary_margin'];ruled=row['every_endpoint_interval_ruled_out']
            if ruled!=(row['intervals_not_ruled_out']==0) or ruled!=(margin<0):raise ValueError('grid interpretation differs')
            ax.add_patch(Rectangle((j-.5,i-.5),1,1,facecolor=colors[int(ruled)],edgecolor='white',linewidth=2))
            ax.text(j,i-.13,f'{margin:+d} choices',ha='center',va='center',fontsize=12,fontweight='bold',color='#24344d')
            ax.text(j,i+.17,f"{row['intervals_not_ruled_out']:,} intervals remain",ha='center',va='center',fontsize=9,color='#334155')
    ax.set_xlim(-.5,4.5);ax.set_ylim(3.5,-.5)
    ax.set_xticks(range(5),['0','4.387188','5.488282','5.555555','11.111111']);ax.set_xlabel('Common selection + submit shift (ms)',labelpad=10)
    ax.set_yticks(range(4),['Encoded / one horizon','Encoded / no target cap','Distinct source / two horizons','Distinct source / no target cap'])
    ax.tick_params(length=0);[spine.set_visible(False) for spine in ax.spines.values()]
    fig.text(.02,.94,'Can any shorter endpoint interval satisfy the necessary counts?',fontsize=18,fontweight='bold',color='#172b4d')
    fig.text(.02,.875,'Maximum over all 509,001 observed intervals of at least 10 s: good-only chain length minus required p95 count.',fontsize=10,color='#334155')
    fig.legend(handles=[Patch(facecolor=colors[1],label='Every interval ruled out in this fixed model'),Patch(facecolor=colors[0],label='Inconclusive: no legal mixed-age path established')],loc='lower left',bbox_to_anchor=(.015,.115),ncol=2,frameon=False,fontsize=10)
    fig.text(.02,.075,'88 Hz endpoint cadence; strict p95 < 15 ms; optimistic publication at receipt; fixed original targets and warm history.',fontsize=10,color='#334155')
    fig.text(.02,.032,'Only these five phases are covered. No runtime, headset, optical, or production qualification result.',fontsize=10,color='#334155')
    fig.savefig(args.out,dpi=170);plt.close(fig)


if __name__=='__main__':main()
