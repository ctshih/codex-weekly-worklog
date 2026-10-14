"""Normalize Codex get_usage_limits snapshots; never infer quota from token sums."""
import argparse
import json
import math
import sys
from datetime import datetime,timezone
from pathlib import Path
from worklog_stats import atomic_json


def normalize(raw,fetched_at=None):
    while isinstance(raw,dict) and 'content' in raw:
        if isinstance(raw['content'],str):
            raw=json.loads(raw['content'])
        elif isinstance(raw['content'],list):
            block=next((x['text'] for x in raw['content'] if isinstance(x,dict) and x.get('type')=='text'),None)
            if not block:
                break
            raw=json.loads(block)
        else:
            break
    buckets=raw.get('rateLimitsByLimitId') or {'codex':raw.get('rateLimits')}
    windows=[]
    for key,bucket in buckets.items():
        if not isinstance(bucket,dict):
            continue
        for name in ('primary','secondary'):
            window=bucket.get(name)
            if not window:
                continue
            minutes=window.get('windowDurationMins')
            used=window.get('usedPercent')
            if used is not None:
                used=float(used)
                if not math.isfinite(used):
                    raise ValueError('nonfinite quota percentage')
                used=max(0,min(100,used))
            reset=window.get('resetsAt')
            label={300:'5 小時',10080:'7 天'}.get(minutes,f'{minutes} 分鐘' if minutes is not None else '未提供視窗長度')
            windows.append(dict(key=f'{key}:{name}',label=f'{bucket.get("limitName") or key} · {label}',used_percent=used,remaining_percent=100-used if used is not None else None,resets_at=datetime.fromtimestamp(reset,timezone.utc).isoformat() if reset is not None else None))
    return dict(fetched_at=fetched_at or datetime.now(timezone.utc).isoformat(),source='Codex account usage limits',windows=windows)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('input',type=Path);parser.add_argument('--out',type=Path,required=True);parser.add_argument('--fetched-at')
    args=parser.parse_args();atomic_json(args.out,normalize(json.loads(args.input.read_text(encoding='utf-8-sig')),args.fetched_at))


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8');main()
