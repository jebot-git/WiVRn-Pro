#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Lossless JSON table packing for the component-model evidence, not a wire codec."""
import argparse
import copy
import json
from pathlib import Path

ROW_KEYS=('case','synthetic_duplicate_eye','profile','rate_mbps','ip_udp_header_bytes','stripe_width','scenario','transfer',
    'modeled_first_record_to_decoded_ms','modeled_server_then_wire_then_client_ms','measured_host_client_cpu_ms',
    'all_modeled_client_finishes_before_ticket')


def pack(source):
    output={k:v for k,v in source.items() if k!='rows'}
    transfers=[];ids={};rows=[]
    for original in source['rows']:
        if set(original)-set(ROW_KEYS):raise ValueError('unrepresented row field')
        row=dict(original);key=json.dumps(row['transfer'],sort_keys=True,separators=(',',':'))
        if key not in ids:ids[key]=len(transfers);transfers.append(row['transfer'])
        row['transfer']=ids[key];rows.append([row.get(k) for k in ROW_KEYS])
    output.update(row_columns=list(ROW_KEYS),row_values=rows,transfer_values=transfers,table_encoding='lossless_json_rows_v1')
    return output


def unpack(source):
    if source['table_encoding']!='lossless_json_rows_v1' or source['row_columns']!=list(ROW_KEYS):raise ValueError('unknown table schema')
    output={k:v for k,v in source.items() if k not in ('row_columns','row_values','transfer_values','table_encoding')}
    rows=[]
    for values in source['row_values']:
        if len(values)!=len(ROW_KEYS):raise ValueError('invalid row length')
        row={k:v for k,v in zip(ROW_KEYS,values) if v is not None};index=row['transfer']
        if type(index) is not int or not 0<=index<len(source['transfer_values']):raise ValueError('invalid transfer index')
        row['transfer']=source['transfer_values'][index];rows.append(row)
    output['rows']=rows;return output


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('source',type=Path);p.add_argument('output',type=Path);a=p.parse_args()
    source=json.loads(a.source.read_text());packed=pack(source)
    if unpack(packed)!=source:raise ValueError('lossless JSON table check failed')
    a.output.write_text(json.dumps(packed,separators=(',',':'))+'\n')
    print(json.dumps(dict(status='pass',rows=len(packed['row_values']),unique_transfers=len(packed['transfer_values']),bytes=a.output.stat().st_size)))


if __name__=='__main__':main()
