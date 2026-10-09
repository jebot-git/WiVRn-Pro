#!/usr/bin/env python3
"""Generate a reviewable pinned catalog; this is not test execution."""
import json
from pathlib import Path
import subprocess
import history

S='prototypes/vr444/experiments/selection_policy/'
P='prototypes/vr444/experiments/pacer_timing/'
C='prototypes/vr444/experiments/client_timeline/'
H='prototypes/vr444/experiments/host_timeline/'
# Exact milestones, never floating branch heads. Notes and positive receipts
# intentionally omit the superseded pre-fix pacer constructor diagnostic.
MILESTONES=(
 ('compositor','420b6025',S,'TRACE-GAP-REPAIR.txt','trace-gap-reanalysis-20261009.json'),
 ('compositor-attempts','ff669ce7',S,'RENDER-ATTEMPTS.txt','attempt-validation-20261009.json'),
 ('compositor-pacing','747a9ed4',S,'HORIZON-RESEARCH.txt','horizon-validation-20261009.json','horizon-counterfactual-20261009.json'),
 ('compositor-reserve','7e7b43a0',S,'READY-SUCCESSOR.txt','ready-successor-validation-20261009.json','ready-successor-counterfactual-20261009.json'),
 ('compositor-pacer-trace','23ac404c',P,'PACER-TRACE.txt','validation-20261009.json'),
 ('compositor-selection-audit','1733d189',S,'SUPPORT-AUDIT.txt','ready-successor-audited-validation-20261009.json','ready-successor-audited-counterfactual-20261009.json'),
 ('compositor-oracle','ecec3fd3',S,'AVAILABILITY-ORACLE.txt','oracle-validation-20261009.json','availability-oracle-20261009.json'),
 ('compositor-timeline','1ad36651',C,'TIMELINE.txt','validation-20261009.json'),
 ('compositor-pacer-init','7d3dfbe4',P,'CONSTRUCTOR-ORDER.txt','constructor-fixed-validation-20261009.json'),
 ('compositor-pacer-capture','bc869a5c',P,'CAPTURE-CONTRACT.txt','capture-validation-20261009.json'),
 ('compositor-target-envelope','60ee8cb8',S,'TARGET-ENVELOPE.txt','envelope-validation-20261009.json','target-envelope-20261009.json'),
 ('compositor-supply-phase','d710ecf0',S,'SUPPLY-PHASE.txt','supply-validation-20261009.json','supply-phase-20261009.json'),
 ('compositor-full-count','f601c7dc',S,'FULL-COUNT.txt','full-count-validation-20261009.json','full-count-20261009.json','full-count-reference-20261009.json'),
 ('compositor-cardinality','45ea2012',S,'CARDINALITY.txt','cardinality-validation-20261009.json','cardinality-20261009.json'),
 ('compositor-publication-ledger','ced4c13e',S,'PUBLICATION-LEDGER.txt','publication-validation-20261009.json'),
 ('compositor-publication-capture','be5fd0bd',S,'PUBLICATION-CAPTURE.txt','publication-capture-validation-20261009.json'),
 ('compositor-phase-frontier','b2076cb1',S,'PHASE-FRONTIER.txt','phase-frontier-validation-20261009.json','phase-frontier-20261009.json','phase-frontier-figure-20261009.json'),
 ('compositor-target-admission','6586e3be',S,'TARGET-ADMISSION.txt','target-admission-validation-20261009.json','target-admission-20261009.json'),
 ('compositor-source-novelty','7876b295',S,'SOURCE-NOVELTY.txt','source-novelty-validation-20261009.json','source-novelty-20261009.json'),
 ('compositor-quantile-bound','169dc9b6',S,'QUANTILE-BOUND.txt','quantile-bound-validation-20261009.json','quantile-bound-20261009.json'),
 ('compositor-host-timeline','d39a89ea',H,'README.txt','host-timeline-validation-20261009.json','host-timeline-20261009.json'),
 ('compositor-receipt-floor','754ab4c4',S,'RECEIPT-FLOOR.txt','receipt-floor-validation-20261009.json','receipt-floor-20261009.json'),
 ('compositor-exact-phase','30692027',S,'EXACT-PHASE.txt','phase-exact-validation-20261009.json','phase-exact-20261009.json'),
 ('compositor-source-receipt-age','5dca6628',S,'SOURCE-RECEIPT-AGE.txt','source-receipt-age-validation-20261009.json','source-receipt-age-20261009.json'),
)


def main():
    read=history.GitBlobs(history.ROOT);approaches=[]
    for name,short,prefix,*files in MILESTONES:
        commit=subprocess.check_output(['git','-C',str(history.ROOT),'rev-parse',short+'^{commit}'],text=True).strip()
        artifacts=[]
        for filename in files:
            path=prefix+filename;raw=read(commit,path)
            kind='notes' if filename.endswith('.txt') else ('figure_receipt' if 'figure' in filename else ('validation' if 'validation' in filename else 'analysis'))
            count=len(history.bindings(json.loads(raw))) if kind!='notes' else 0
            artifact=dict(path=path,kind=kind,sha256=history.digest(raw),source_bindings=count)
            if filename=='phase-frontier-validation-20261009.json':
                artifact['known_source_gaps']=[dict(path=S+'plot_phase_frontier.py',
                    recorded_sha256='5a226fbc25ca24704224ae6934d5f58cccc028c2ba309affa0cf39b1000a8290',
                    committed_sha256='7c95865e9e2b6a63ab8219e88376c43ba473d406555b0bc0dd41fbe6e5a3e2d6',
                    reason='Validation preceded the final plot layout edit. The separately pinned figure-render receipt binds the final plot, data and PNG; it does not retroactively change the original test receipt.')]
            artifacts.append(artifact)
        approaches.append(dict(id=name,branch='research/sub15-20261009/'+name,commit=commit,artifacts=artifacts))
    result=dict(schema=1,kind='pinned_compositor_research_catalog',approaches=approaches,
        native_integration_required=['Regenerate integration.patch and overlay-manifest in the integration owner worktree.',
            'Include compositor-pacer-init when integrating compositor-pacer-trace; the original trace milestone precedes the constructor-order fix.',
            'Perform a linked native build and qualification before any future authorized headset test.'],
        headset_testing_suspended=True,promotion_eligible=False)
    history.verify(result,read)
    out=history.HERE/'catalog.json'
    with out.open('x') as stream:json.dump(result,stream,indent=2);stream.write('\n')
    print('wrote',out)


if __name__=='__main__':main()
