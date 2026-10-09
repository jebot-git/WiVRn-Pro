#!/usr/bin/env python3
"""Render exact finite-grid quantile bounds, without interpolated claims."""
import argparse
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input',type=Path);parser.add_argument('output',type=Path)
    args=parser.parse_args();data=json.loads(args.input.read_text())
    if data.get('status')!='pass' or data.get('analysis_kind')!='exact_quantile_at_sufficient_cadence_count':
        raise ValueError('expected exact quantile result')
    groups={name:[a for a in data['alternatives'] if a['model']==name] for name in
        ('encoded_one_horizon','encoded_unbounded','source_unbounded','source_two_horizons')}
    if any(a['minimum_p95_ns']!=b['minimum_p95_ns'] for a,b in zip(groups['source_unbounded'],groups['source_two_horizons'])):
        raise ValueError('source horizon curves differ; plot labels require revision')
    fig,ax=plt.subplots(figsize=(10.5,6.4))
    styles=(('encoded_one_horizon','#b64151','Encoded IDs, one-horizon target cap'),
            ('encoded_unbounded','#117c92','Encoded IDs, no target cap'),
            ('source_unbounded','#976016','Distinct source sets, no cap / two horizons'))
    for name,color,label in styles:
        rows=groups[name]
        ax.plot([r['sampling_shift_ns']/1e6 for r in rows],[r['minimum_p95_ns']/1e6 for r in rows],
            color=color,marker='o',markersize=4,linewidth=1.8,label=label)
    ax.axhline(15,color='#27303b',linestyle='--',linewidth=1,label='15 ms reference (strictly below required)')
    ax.set(xlabel='Hypothetical shift of selection and submission (ms)',ylabel='Exact minimum nearest-rank p95 receive age (ms)',
        xlim=(-.2,11.35),ylim=(14,28),xticks=range(12))
    ax.grid(alpha=.2);ax.legend(loc='upper left',fontsize=9)
    ax.annotate('14.874793 ms',xy=(4,14.874793),xytext=(5.3,14.3),color='#117c92',
        arrowprops=dict(arrowstyle='->',color='#117c92'))
    ax.annotate('15.936408 ms',xy=(5.5,15.936408),xytext=(7.2,15.7),color='#976016',
        arrowprops=dict(arrowstyle='->',color='#976016'))
    ax.annotate('19.241095 ms',xy=(0,19.241095),xytext=(.6,17.7),color='#b64151',
        arrowprops=dict(arrowstyle='->',color='#b64151'))
    fig.suptitle('Exact optimistic p95 depends on phase, target lead and source identity',fontsize=14,weight='bold',y=.98)
    ax.set_title('1,875 choices • 8 ms hypothetical publication advance • 25 phases only; lines connect sampled points',fontsize=10,pad=12)
    fig.text(.075,.055,'Sufficient full-window count for 88 Hz • unlimited known-pair retention • original runtime targets fixed\n'
        'Not a continuous-phase optimum, runnable policy, optical latency measurement or headset performance result.',fontsize=9,color='#46515c')
    fig.subplots_adjust(left=.10,right=.97,bottom=.22,top=.84)
    fig.savefig(args.output,dpi=150)


if __name__=='__main__':main()
