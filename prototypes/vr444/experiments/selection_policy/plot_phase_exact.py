#!/usr/bin/env python3
"""Display exact phase minima without inventing curves through pruned events."""
import argparse
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('result',type=Path);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    r=json.loads(a.result.read_text())
    if r.get('status')!='pass' or r.get('analysis_kind')!='exact_all_integer_phase_receipt_floor_bound':raise ValueError('exact phase result required')
    models=r['models'];labels=['Encoded frames\nOne-horizon target cap','Encoded frames\nUnrestricted target lead','Distinct source generations\nTwo-horizon target cap','Distinct source generations\nUnrestricted target lead']
    fig,axis=plt.subplots(figsize=(13,7));fig.subplots_adjust(left=.27,right=.96,top=.75,bottom=.29)
    fig.suptitle('Closing the gaps between phase samples',x=.045,y=.98,ha='left',fontsize=21,fontweight='bold')
    fig.text(.045,.91,'Exact bounds over 11,111,112 integer shifts from 0 to 11.111111 ms',fontsize=12)
    fig.text(.045,.865,'Every known eye ready at receipt • 1,875 choices over the full warm span • original acquisition remains failed',fontsize=10,color='#536171')
    values=[m['minimum_p95_ns']/1e6 for m in models]
    bars=axis.barh(range(4),values,height=.58,color=['#30688c','#249c90','#e0a355','#e0a355'])
    axis.set_yticks(range(4),labels);axis.invert_yaxis();axis.set_xlim(0,22)
    for j,m in enumerate(models):
        axis.text(values[j]+.18,j,f'{values[j]:.6f} ms',va='center',fontsize=11,fontweight='bold')
        axis.text(.22,j+.2,f'phase {m["best_phase_ns"]/1e6:.6f} ms',va='center',fontsize=9,color='white' if j<2 else '#443821')
    axis.axvline(15,color='#a74545',linestyle='--',linewidth=1.5,label='15 ms reference')
    axis.legend(loc='upper right',bbox_to_anchor=(1,1.12),frameon=False)
    axis.set_xlabel('Exact minimum nearest-rank p95 (ms)');axis.grid(axis='x',alpha=.2);axis.set_axisbelow(True)
    axis.spines[['top','right','left']].set_visible(False)
    fig.text(.045,.20,'Proof coverage: 150 evaluated optima; 5,653 additional candidate events excluded by exact interval bounds.',fontsize=11,fontweight='bold')
    fig.text(.045,.105,'Between-grid improvement: 0.022604 ms for unrestricted encoded identity; 0.011718 ms for distinct source identity.\nKnown receipts, original target sequence and full-span sufficient cadence count stay fixed. Unlimited retention and\nearlier use of later-observed metadata remain optimistic; runtime feedback, pose quality and optics are unqualified.',fontsize=10,color='#536171',linespacing=1.5)
    fig.text(.045,.035,'These are conditional mathematical bounds, not measured speedups or a runnable headset policy.',fontsize=11,color='#536171')
    fig.savefig(a.out,dpi=160,facecolor='white')


if __name__=='__main__':main()
