"""Codex daily worklog: read-only local rollouts, exact time-window usage.

Modern response_id usage is authoritative. Legacy cumulative snapshots contribute
positive deltas before the first modern usage record; rate-limit refreshes do not
add tokens. input includes cached input; output includes reasoning output.
Only top-level human input is counted. Child usage belongs to one root; unlinked
internal/guardian usage remains a separate visible session, never silently lost.
"""
import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

FIELDS = ('input_tokens','cached_input_tokens','output_tokens','reasoning_output_tokens','total_tokens')


def parse_time(value):
    if isinstance(value,(int,float)):
        return datetime.fromtimestamp(value/1000 if value>1e11 else value,timezone.utc)
    result=datetime.fromisoformat(str(value).replace('Z','+00:00'))
    if result.tzinfo is None:
        raise ValueError('timestamp requires timezone')
    return result


def fixed_tz(value):
    if value in ('UTC','Z'):
        return timezone.utc
    match=re.fullmatch(r'([+-])(\d{2}):(\d{2})',value)
    if not match or int(match[2])>23 or int(match[3])>59:
        raise ValueError('timezone must be UTC or an offset such as +08:00')
    return timezone((1 if match[1]=='+' else -1)*timedelta(hours=int(match[2]),minutes=int(match[3])))


def text_content(content):
    if isinstance(content,str):
        return content
    if not isinstance(content,list):
        return ''
    return '\n'.join(x.get('text','') for x in content if isinstance(x,dict) and str(x.get('type','')).lower() in ('text','input_text','output_text'))


def human_text(text, canonical=False):
    if canonical:
        return text.strip()
    # Legacy response_item includes injected user context. Preserve canonical UI
    # UserMessage verbatim; this filter applies only to legacy transport context.
    if text.startswith(('# AGENTS.md instructions','<environment_context>','<external_codex_apps_open_page>','<permissions instructions>','<collaboration_mode>','<turn_aborted>')):
        return ''
    if text.startswith(('Base directory for this skill','Message Type: MESSAGE','Message Type: FINAL_ANSWER')):
        return ''
    return text.strip()


def usage_values(raw):
    values={k:max(0,int(raw.get(k,0) or 0)) for k in FIELDS}
    if 'total_tokens' not in raw:
        values['total_tokens']=values['input_tokens']+values['output_tokens']
    return values


def read_rollout(path,start,end,warnings,expected_id=None):
    modern,legacy,canonical_users,legacy_users,transport_users,agents=[],[],[],[],[],[]
    metadata,completed,turn_id,model,baseline={},set(),None,'unknown',None
    triggers={};last_started=None
    active=False
    with path.open('rb') as stream:
        limit=os.fstat(stream.fileno()).st_size
        number=0
        while stream.tell()<limit:
            line=stream.readline(limit-stream.tell());number+=1
            try:
                record=json.loads(line)
                timestamp=parse_time(record['timestamp'])
            except (ValueError,KeyError,UnicodeDecodeError,TypeError):
                warnings.append(f'{path.name}:{number} 無法解析，可能尚未寫完')
                continue
            if timestamp>=end:
                continue
            p=record.get('payload') or {};kind=record.get('type')
            if expected_id and p.get('thread_id') and p['thread_id']!=expected_id and kind in ('token_usage_record','event_msg'):
                continue
            if kind=='session_meta':
                metadata={k:p.get(k) for k in ('id','session_id','cwd','source','thread_source','parent_thread_id','subagent_history_start_ordinal')}
            if metadata.get('subagent_history_start_ordinal') is not None and kind!='session_meta' and record.get('ordinal',number)<metadata['subagent_history_start_ordinal']:
                continue
            if kind=='turn_context':
                model=p.get('model') or 'unknown';turn_id=p.get('turn_id') or turn_id
            if kind=='event_msg' and p.get('type')=='task_started':
                turn_id=p.get('turn_id') or turn_id
                last_started=turn_id
                triggers[turn_id]=(p.get('turn_attribution') or {}).get('turn_trigger') or p.get('turn_trigger')
            if kind=='event_msg' and p.get('type') in ('task_complete','turn_aborted'):
                completed.add(p.get('turn_id') or turn_id)
            if start<=timestamp<end:
                active=True
            if kind=='token_usage_record' and p.get('usage'):
                modern.append(dict(at=timestamp,id=p.get('response_id') or f'ordinal:{record.get("ordinal",number)}',model=p.get('model') or model,usage=usage_values(p['usage']),turn_id=p.get('turn_id') or turn_id))
            if kind=='event_msg' and p.get('type')=='token_count' and p.get('info'):
                info=p['info'];total=usage_values(info.get('total_token_usage') or {})
                if baseline is None:
                    delta=usage_values(info.get('last_token_usage') or {})
                elif any(total[k]<baseline[k] for k in FIELDS):
                    delta=usage_values(info.get('last_token_usage') or total)
                    if start<=timestamp<end:
                        warnings.append(f'{path.name} 累積 token 計數重置，以當次紀錄接續')
                else:
                    delta={k:total[k]-baseline[k] for k in FIELDS}
                baseline=total
                if delta['total_tokens'] or delta['input_tokens'] or delta['output_tokens']:
                    legacy.append(dict(at=timestamp,id=f'legacy:{number}',model=model,usage=delta,turn_id=turn_id))
            if kind=='event_msg' and p.get('type')=='item_completed':
                item=p.get('item') or {};typ=str(item.get('type','')).lower()
                message=dict(at=timestamp,id=item.get('client_id') or item.get('clientId') or item.get('id') or f'line:{number}',turn_id=p.get('turn_id') or turn_id)
                if typ=='usermessage':
                    text=human_text(text_content(item.get('content')),True)
                    if text:
                        canonical_users.append(dict(message,text=text,client_id=item.get('client_id') or item.get('clientId')))
                elif typ=='agentmessage' and item.get('text'):
                    agents.append(dict(message,text=item['text'],phase=item.get('phase')))
            elif kind=='event_msg' and p.get('type')=='user_message':
                text=human_text(p.get('message',''))
                if text:
                    legacy_users.append(dict(at=timestamp,id=p.get('id') or f'legacy-user:{timestamp.isoformat()}:{hashlib.sha256(text.encode()).hexdigest()[:12]}',turn_id=turn_id,text=text))
            elif kind=='event_msg' and p.get('type')=='agent_message' and p.get('message'):
                agents.append(dict(at=timestamp,id=f'legacy-agent:{number}',turn_id=turn_id,text=p['message'],phase=p.get('phase')))
            if kind=='response_item' and p.get('type')=='message':
                text=text_content(p.get('content'))
                if p.get('role')=='user' and human_text(text):
                    transport_users.append(dict(at=timestamp,id=p.get('id') or f'transport:{number}',turn_id=turn_id,text=human_text(text)))
                elif p.get('role')=='assistant' and text:
                    agents.append(dict(at=timestamp,id=p.get('id') or f'transport-agent:{number}',turn_id=turn_id,text=text,phase=p.get('phase')))
    # Prefer UI items when available. Older segments can coexist after upgrade.
    users=canonical_users+([x for x in legacy_users if not canonical_users or x['at']<min(y['at'] for y in canonical_users)])
    if not users:
        users=transport_users
    automated=[];human=[]
    for u in users:
        trigger=triggers.get(u.get('turn_id')) or ''
        is_auto=trigger.startswith(('automation_','goal_auto','agent_')) or trigger=='heartbeat' or (not trigger and not u.get('client_id') and u['text'].startswith('<heartbeat>') and '<automation_id>' in u['text'])
        (automated if is_auto else human).append(u)
    users=human
    users=list({x['id']:x for x in users}.values());users.sort(key=lambda x:x['at'])
    agent_unique={}
    for a in agents:
        key=(a['text'],a.get('turn_id'))
        if key not in agent_unique or a['at']<agent_unique[key]['at']:
            agent_unique[key]=a
    agents=sorted(agent_unique.values(),key=lambda x:x['at'])
    first_modern=min((x['at'] for x in modern),default=end)
    responses={}
    for item in modern:
        previous=responses.get(item['id'])
        responses[item['id']]=dict(item,at=min(previous['at'],item['at']) if previous else item['at'])
    measurements=list(responses.values())+[x for x in legacy if x['at']<first_modern]
    measurements=[x for x in measurements if start<=x['at']<end]
    usage={}
    for item in measurements:
        u=usage.setdefault(item['model'],dict(records=0,**{k:0 for k in FIELDS}))
        u['records']+=1
        for k in FIELDS:
            u[k]+=item['usage'][k]
    requests=[x for x in users if start<=x['at']<end]
    previous=[x for x in users if x['at']<start]
    if previous and (requests or any(start<=a['at']<end for a in agents) or measurements):
        requests=[dict(previous[-1],carried_in=True)]+requests
    turns=[]
    for request in requests:
        next_time=next((u['at'] for u in users if u['at']>request['at']),end)
        replies=[a['text'] for a in agents if max(start,request['at'])<=a['at']<min(next_time,end)]
        pending=request.get('turn_id') not in completed
        fingerprint=hashlib.sha256(json.dumps([request['text'],replies,pending],ensure_ascii=False).encode()).hexdigest()
        turns.append(dict(key=request['id'],turn_id=request.get('turn_id'),timestamp=request['at'].isoformat(),text=request['text'],deliveries=replies,in_progress=pending,carried_in=request.get('carried_in',False),fingerprint=fingerprint))
    evidence=[a['text'] for a in agents if start<=a['at']<end]
    return dict(meta=metadata,usage=usage,measurements=measurements,user_turns=turns,delivery_evidence=evidence,automated_inputs=sum(start<=u['at']<end for u in automated),in_progress=bool(last_started and last_started not in completed),active=active,first_modern=first_modern.isoformat() if modern else None)


def collect(home,start,end):
    home=home.resolve();warnings=[];metadata={};parents={}
    databases=sorted(home.glob('state_*.sqlite'),key=lambda x:int(x.stem.split('_')[-1]))
    if databases:
        with closing(sqlite3.connect(databases[-1].as_uri()+'?mode=ro',uri=True,timeout=5)) as db:
            db.row_factory=sqlite3.Row
            columns={r[1] for r in db.execute('PRAGMA table_info(threads)')}
            selected=[k for k in ('id','name','title','cwd','rollout_path','source','thread_source','model') if k in columns]
            for row in db.execute('SELECT '+','.join(selected)+' FROM threads'):
                metadata[row['id']]=dict(row)
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='thread_spawn_edges'").fetchone():
                parents.update((r[1],r[0]) for r in db.execute('SELECT parent_thread_id,child_thread_id FROM thread_spawn_edges'))
    paths={}
    for r in metadata.values():
        if r.get('rollout_path'):
            p=Path(r['rollout_path'])
            if p.exists() and p.resolve().is_relative_to(home):
                paths[r['id']]=p
    for folder in ('sessions','archived_sessions'):
        for p in (home/folder).rglob('*.jsonl'):
            match=re.search(r'([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})',p.name)
            if match:
                paths.setdefault(match[1],p)
    parsed={}
    for sid,path in paths.items():
        # mtime is only a candidate filter, never the report's interaction date.
        if datetime.fromtimestamp(path.stat().st_mtime,timezone.utc)<start:
            continue
        try:
            info=read_rollout(path,start,end,warnings,sid)
        except OSError as exc:
            warnings.append(f'{path.name} 無法讀取：{type(exc).__name__}');continue
        if not info['usage'] and not info['delivery_evidence'] and not any(not t.get('carried_in') for t in info['user_turns']):
            continue
        r=metadata.setdefault(sid,dict(info['meta'],id=sid))
        if info['meta'].get('parent_thread_id'):
            parents.setdefault(sid,info['meta']['parent_thread_id'])
        source=r.get('source') or info['meta'].get('source') or ''
        if isinstance(source,str) and source.startswith('{'):
            try:
                source=json.loads(source)
            except ValueError:
                pass
        if isinstance(source,dict):
            sub=source.get('subagent') or {}
            spawn=sub.get('thread_spawn') or {}
            if spawn.get('parent_thread_id'):
                parents.setdefault(sid,spawn['parent_thread_id'])
        parsed[sid]=info
    def root(sid):
        seen=set()
        while sid in parents and sid not in seen:
            seen.add(sid);sid=parents[sid]
        if sid in seen:
            warnings.append('子代理父子關係有循環；以可辨識來源分列')
        return sid
    grouped=defaultdict(list)
    for sid in parsed:
        grouped[root(sid)].append(sid)
    sessions=[]
    for root_id,members in grouped.items():
        r=metadata.get(root_id,{})
        source=r.get('source') or ''
        internal=r.get('thread_source') in ('subagent','guardian_review') or 'subagent' in str(source)
        main=parsed.get(root_id)
        if main is None and root_id in paths:
            main=read_rollout(paths[root_id],start,end,warnings,root_id)
        main=main or dict(user_turns=[],delivery_evidence=[],usage={})
        sources=[]
        for sid in sorted(set(members)):
            item=parsed[sid];m=metadata.get(sid,{})
            category='主控' if sid==root_id else '內部審核' if m.get('thread_source')=='guardian_review' else '子代理'
            sources.append(dict(id=sid,name=(m.get('name') or m.get('title') or sid),kind=category,usage=item['usage']))
        title=r.get('name') or r.get('title') or root_id
        turns=[] if internal else main['user_turns']
        session=dict(key=root_id,title=title,cwd=r.get('cwd') or main.get('meta',{}).get('cwd'),link='codex://threads/'+root_id,internal=internal,user_turns=turns,automated_inputs=main.get('automated_inputs',0),in_progress=main.get('in_progress',False),delivery_evidence=main['delivery_evidence'],sources=sources)
        session['fingerprint']=hashlib.sha256(json.dumps([[(t['key'],t['fingerprint']) for t in turns],session['delivery_evidence'],session['in_progress']],ensure_ascii=False).encode()).hexdigest()
        sessions.append(session)
    sessions.sort(key=lambda s:(s['internal'],min((t['timestamp'] for t in s['user_turns']),default=end.isoformat()),s['key']))
    return dict(schema_version=1,start=start.isoformat(),end=end.isoformat(),collected_at=datetime.now(timezone.utc).isoformat(),host=os.environ.get('COMPUTERNAME') or os.uname().nodename if hasattr(os,'uname') else os.environ.get('COMPUTERNAME','unknown'),scope='本機 Codex 對話紀錄，含封存及可取得的子代理；不宣稱涵蓋遠端／雲端',sessions=sessions,warnings=list(dict.fromkeys(warnings)))


def atomic_json(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.'+str(os.getpid())+'.tmp')
    tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    os.replace(tmp,path)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('date');parser.add_argument('start',nargs='?',default='00:00')
    parser.add_argument('--end');parser.add_argument('--tz',default=os.environ.get('CODEX_WORKLOG_TZ','+08:00'))
    parser.add_argument('--codex-home',type=Path,default=Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex'))))
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();tz=fixed_tz(args.tz)
    start=datetime.fromisoformat(args.date+'T'+args.start).replace(tzinfo=tz)
    day_end=start.replace(hour=0,minute=0)+timedelta(days=1)
    end=datetime.fromisoformat(args.date+'T'+args.end).replace(tzinfo=tz) if args.end else min(datetime.now(tz),day_end)
    if not start<end<=day_end:
        parser.error('end must be after start and within the selected day')
    atomic_json(args.out,collect(args.codex_home,start,end))
    print(str(args.out.resolve()))


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8');main()
