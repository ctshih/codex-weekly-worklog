"""Collect seven complete local days, ending yesterday or --through YYYY-MM-DD."""
import argparse
import os
import sys
from datetime import datetime,timedelta
from pathlib import Path
import worklog_stats as engine


def week_bounds(tz,through=None,now=None):
    today=(now or datetime.now(tz)).astimezone(tz).replace(hour=0,minute=0,second=0,microsecond=0)
    end=datetime.strptime(through,'%Y-%m-%d').replace(tzinfo=tz)+timedelta(days=1) if through else today
    if end>today:
        raise ValueError('截止日必須是已結束的日期；今天日報請使用 worklog_stats.py')
    return end-timedelta(days=7),end


def collect_week(home,start,end):
    original=engine.read_rollout
    daily={str((start+timedelta(days=i)).date()):dict(usage={k:0 for k in engine.FIELDS},human_inputs=0,threads=set()) for i in range(7)}
    seen=set()
    def capture(path,start,end,warnings,expected_id=None):
        info=original(path,start,end,warnings,expected_id)
        identity=expected_id or str(path.resolve())
        if identity not in seen:
            seen.add(identity)
            for item in info.get('measurements',[]):
                day=daily[str(item['at'].astimezone(start.tzinfo).date())]
                day['threads'].add(identity)
                for k in engine.FIELDS:day['usage'][k]+=item['usage'][k]
        return info
    engine.read_rollout=capture
    try:stats=engine.collect(home,start,end)
    finally:engine.read_rollout=original
    for session in stats['sessions']:
        for turn in session['user_turns']:
            if not turn.get('carried_in'):
                daily[str(engine.parse_time(turn['timestamp']).astimezone(start.tzinfo).date())]['human_inputs']+=1
    for day in daily.values():day['threads']=len(day['threads'])
    for k in engine.FIELDS:
        total=sum(u[k] for s in stats['sessions'] for source in s['sources'] for u in source['usage'].values())
        if total!=sum(d['usage'][k] for d in daily.values()):raise ValueError('逐日與全週用量不一致：'+k)
    stats.update(report_kind='weekly',daily=daily)
    return stats


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--through',help='最後包含的日期；預設昨天')
    parser.add_argument('--tz',default=os.environ.get('CODEX_WORKLOG_TZ','+08:00'))
    parser.add_argument('--codex-home',type=Path,default=Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex'))))
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    try:start,end=week_bounds(engine.fixed_tz(args.tz),args.through)
    except ValueError as error:parser.error(str(error))
    engine.atomic_json(args.out,collect_week(args.codex_home,start,end))
    print(str(args.out.resolve()))


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8');main()
