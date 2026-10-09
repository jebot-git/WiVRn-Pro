#!/usr/bin/env python3
"""Render known-source-age optimum brackets without inventing an exact optimum."""
import argparse
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('result',type=Path);parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();result=json.loads(args.result.read_text())
    if result.get('status')!='pass' or result.get('analysis_kind')!='known_source_receipt_age_optimum_brackets':raise ValueError('audited source-age brackets required')
    expected=['encoded_one_horizon','encoded_unbounded','source_two_horizons','source_unbounded']
    rows={r['model']:r for r in result['models']}
    if len(result['models'])!=4 or set(rows)!=set(expected):raise ValueError('complete four-model population required')
    labels=['Encoded frames · one horizon','Encoded frames · no target ceiling','Distinct sources · two horizons','Distinct sources · no target ceiling']
    fig,ax=plt.subplots(figsize=(11.2,5.4),dpi=180);fig.patch.set_facecolor('#f7f8fa');ax.set_facecolor('#f7f8fa')
    for i,(name,label) in enumerate(zip(expected,labels)):
        r=rows[name];low=r['minimum_known_source_p95_lower_bound_ns'];high=r['minimum_known_source_p95_upper_bound_ns']
        if any(type(v) is not int for v in (low,high)) or not 0<=low<=high:raise ValueError('invalid exact bracket')
        y=3-i;color='#367aa3' if high<15_000_000 else '#aa6337'
        ax.plot([low/1e6,high/1e6],[y,y],color=color,linewidth=7,solid_capstyle='butt')
        ax.plot(low/1e6,y,marker='|',color=color,markersize=17,markeredgewidth=2)
        ax.plot(high/1e6,y,marker='|',color=color,markersize=17,markeredgewidth=2)
        before=high>18_000_000
        ax.annotate(f'[{low/1e6:.6f}, {high/1e6:.6f}] ms',((low if before else high)/1e6,y),
            xytext=(-10 if before else 10,0),textcoords='offset points',ha='right' if before else 'left',va='center',fontsize=10,color='#263442')
    ax.axvline(15,color='#66717c',linestyle=(0,(4,4)),linewidth=1.1)
    ax.text(15.025,3.48,'15 ms threshold',fontsize=9,color='#52606d')
    ax.set(yticks=[3,2,1,0],yticklabels=labels,xlim=(14.55,20.62),ylim=(-.55,3.6),xlabel='Minimum known-source receipt-age p95 (ms)')
    ax.grid(axis='x',alpha=.18);ax.set_axisbelow(True);ax.tick_params(axis='y',length=0,pad=10,labelsize=10)
    for spine in ax.spines.values():spine.set_visible(False)
    fig.suptitle('Source-age bounds preserve the model’s 15 ms distinction',x=.055,ha='left',fontsize=16,fontweight='bold',color='#203342')
    fig.text(.055,.89,'Lower bound: exact encoded-age optimum     Upper bound: independently rescored legal witness',fontsize=10,color='#52606d')
    fig.text(.055,.055,'Fixed recorded model: 1,875 choices; phases 0–11.111111 ms; zero publication delay; original receipts and targets.\nIntervals are not exact source-age minima. No headset, optical-latency or production qualification claim.',fontsize=9,color='#52606d',linespacing=1.5)
    fig.subplots_adjust(left=.315,right=.975,top=.82,bottom=.23)
    if args.out.exists():raise ValueError('refuse to overwrite figure')
    fig.savefig(args.out,facecolor=fig.get_facecolor());plt.close(fig)


if __name__=='__main__':main()
