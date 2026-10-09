#!/usr/bin/env python3
"""Plot recorded host-local intervals without combining host/client clocks."""
import argparse
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('result',type=Path);parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();r=json.loads(args.result.read_text())
    if r.get('status')!='pass' or r.get('analysis_kind')!='selected_host_local_callback_timeline':
        raise ValueError('validated host timeline required')
    fig,(a,b)=plt.subplots(2,1,figsize=(13,8.6),gridspec_kw={'height_ratios':[1.1,1]})
    fig.subplots_adjust(left=.19,right=.96,top=.81,bottom=.23,hspace=.65)
    fig.suptitle('Host callbacks for actual selected frames',x=.05,ha='left',fontsize=20,fontweight='bold',y=.98)
    fig.text(.05,.92,'Run 05 • 1,900 warm first encoded submissions • original acquisition remains failed',fontsize=11,color='#536171')
    groups=('warm_first_encoded','warm_first_host_latch','warm_repeated_host_latch')
    labels=['All selected\n(n = 1,900)','First host latch\n(n = 1,886)','Repeated host latch\n(n = 14)']
    for j,(quantile,color) in enumerate((('p50','#1e618c'),('p95','#31a39b'),('p99','#edac56'))):
        values=[r[k]['stage_ms']['wake_to_accepted_input_ns'][quantile] for k in groups]
        bars=a.barh(np.arange(3)+(j-1)*.24,values,height=.21,label=quantile,color=color)
        a.bar_label(bars,labels=[f'{v:.3f}' for v in values],padding=4,fontsize=9)
    a.set_yticks(np.arange(3),labels);a.invert_yaxis();a.set_xlim(0,16.7)
    a.set_title('Wake → accepted compositor input: nearest-rank quantiles',loc='left',pad=15,fontweight='bold')
    a.set_xlabel('Host-local elapsed time (ms)')
    handles,legend_labels=a.get_legend_handles_labels()
    fig.legend(handles,legend_labels,ncol=3,loc='upper left',bbox_to_anchor=(.185,.9),frameon=False)
    samples=[r['warm_first_encoded']['p95_total_ranked_sample'],r['warm_first_host_latch']['five_largest_total_samples'][0],r['warm_repeated_host_latch']['five_largest_total_samples'][0]]
    sample_labels=[f'Actual p95-ranked frame {samples[0]["encoded_frame"]}',f'Largest first-latch frame {samples[1]["encoded_frame"]}',f'Largest relatch frame {samples[2]["encoded_frame"]}']
    stages=(('wake_to_begin_ns','Wake → begin','#1e618c'),('begin_to_delivered_ns','Begin → delivery','#31a39b'),('delivered_to_gpu_done_ns','Delivery → GPU done','#8abb5d'),('gpu_done_to_latch_ns','GPU done → latch','#edac56'),('latch_to_accepted_input_ns','Latch → acceptance','#a971a4'))
    left=np.zeros(3)
    for key,label,color in stages:
        values=np.array([s['intervals_ns'][key]/1e6 for s in samples])
        b.barh(np.arange(3),values,left=left,height=.5,label=label,color=color);left+=values
    for j,v in enumerate(left):b.text(v+.15,j,f'{v:.6f}',va='center',fontsize=9)
    b.set_yticks(np.arange(3),sample_labels);b.invert_yaxis();b.set_xlim(0,16.7)
    b.set_title('Three individual frame decompositions: components add exactly',loc='left',pad=15,fontweight='bold')
    b.set_xlabel('Host-local elapsed time (ms)')
    handles,legend_labels=b.get_legend_handles_labels()
    fig.legend(handles,legend_labels,ncol=3,loc='upper left',bbox_to_anchor=(.045,.145),frameon=False,fontsize=9)
    for axis in (a,b):
        axis.spines[['top','right','left']].set_visible(False);axis.grid(axis='x',alpha=.2);axis.set_axisbelow(True)
    fig.text(.05,.035,'The five components add only within each individual frame; stage quantiles must not be added.\nPrediction lead (48.56 ms median) is a signed target phase, not observed waiting or removable latency.\nNo host/client clock subtraction, optical endpoint, measured speedup or promotion claim.',fontsize=10,color='#536171',linespacing=1.5)
    fig.savefig(args.out,dpi=160,facecolor='white')


if __name__=='__main__':main()
