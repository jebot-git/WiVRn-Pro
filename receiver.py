#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Example read-only subscriber. Run several copies against one bridge."""
import argparse
import asyncio
import json
from pathlib import Path
from bridge import VERSION, LIMIT, default_directory, encode


async def subscribe(endpoint):
    config = json.loads(Path(endpoint).read_text())
    if config['host'] != '127.0.0.1' or config['version'] != VERSION:
        raise ValueError('Unsupported endpoint')
    reader, writer = await asyncio.open_connection(config['host'], config['port'], limit=LIMIT)
    try:
        writer.write(encode({'version': VERSION, 'op': 'subscribe', 'token': config['token']}))
        await writer.drain()
        while True:
            # Disconnect or two seconds without traffic invalidates all cached live poses.
            line = await asyncio.wait_for(reader.readline(), 2)
            if not line:
                return
            yield json.loads(line)
    finally:
        writer.close()
        await writer.wait_closed()


async def main(args):
    seen = 0
    async for packet in subscribe(args.endpoint or default_directory() / 'endpoint.json'):
        print(json.dumps(packet), flush=True)
        seen += 1
        if args.samples and seen >= args.samples:
            break


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--endpoint', type=Path)
    parser.add_argument('--samples', type=int, default=0)
    asyncio.run(main(parser.parse_args()))
