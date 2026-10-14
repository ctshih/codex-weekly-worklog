import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import datetime,timezone
from pathlib import Path

SCRIPTS=Path(__file__).resolve().parents[1]/'codex-weekly-worklog'/'scripts'
sys.path.insert(0,str(SCRIPTS))
import worklog_stats as stats
import worklog_render as render
import usage_json
import selfcheck
import worklog_week as week


def record(at,kind,payload):
    return dict(timestamp=at,type=kind,payload=payload)


def token(at,response,inp,cache,out,reason=0,thread=None):
    return record(at,'token_usage_record',dict(response_id=response,thread_id=thread,usage=dict(input_tokens=inp,cached_input_tokens=cache,output_tokens=out,reasoning_output_tokens=reason,total_tokens=inp+out)))


def user(at,identifier,text,turn='turn'):
    return record(at,'event_msg',dict(type='item_completed',turn_id=turn,item=dict(type='UserMessage',id=identifier,content=[dict(type='text',text=text)])))


class WorklogTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.start=stats.parse_time('2026-10-12T00:00:00+08:00');self.end=stats.parse_time('2026-10-13T00:00:00+08:00')
    def tearDown(self):
        self.temp.cleanup()
    def write(self,name,events):
        path=self.root/name;path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text('\n'.join(json.dumps(x,ensure_ascii=False) for x in events)+'\n',encoding='utf-8');return path
    def test_cumulative_boundary_modern_mirror_and_reasoning(self):
        def cumulative(at,inp,cache,out):
            usage=dict(input_tokens=inp,cached_input_tokens=cache,output_tokens=out,total_tokens=inp+out)
            return record(at,'event_msg',dict(type='token_count',info=dict(total_token_usage=usage,last_token_usage=usage)))
        path=self.write('fixture.jsonl',[
            record('2026-10-11T15:59:00Z','turn_context',dict(model='gpt-test',turn_id='turn')),
            user('2026-10-11T15:59:01Z','carry','跨日要求'),
            cumulative('2026-10-11T15:59:20Z',100,60,10),
            cumulative('2026-10-11T16:00:10Z',150,90,15),
            cumulative('2026-10-11T16:00:11Z',150,90,15),
            token('2026-10-11T16:00:20Z','response',250,200,25,5),
            cumulative('2026-10-11T16:00:21Z',400,290,40),
            token('2026-10-11T16:00:22Z','response',250,200,25,5),
            user('2026-10-12T01:00:01Z','one','重複要求'),user('2026-10-12T01:00:20Z','two','重複要求'),
            user('2026-10-12T01:00:30Z','literal','<environment_context>我親自輸入的文字</environment_context>'),
            record('2026-10-12T01:01:00Z','event_msg',dict(type='task_complete',turn_id='turn'))])
        parsed=stats.read_rollout(path,self.start,self.end,[])
        usage=parsed['usage']['gpt-test']
        self.assertEqual(usage['total_tokens'],330)
        self.assertEqual(usage['input_tokens'],300)
        self.assertEqual(usage['cached_input_tokens'],230)
        self.assertEqual(usage['reasoning_output_tokens'],5)
        self.assertEqual(len(parsed['user_turns']),4)
        self.assertTrue(parsed['user_turns'][0]['carried_in'])
        self.assertFalse(parsed['user_turns'][-1]['in_progress'])
        self.assertEqual(render.columns(usage),(70,230,30,330))
    def test_replayed_response_does_not_cross_daily_boundary(self):
        path=self.write('replay.jsonl',[token('2026-10-11T15:59:00Z','same',10,5,2),token('2026-10-11T16:00:10Z','same',10,5,2)])
        self.assertEqual(stats.read_rollout(path,self.start,self.end,[])['usage'],{})
    def test_child_usage_belongs_to_one_parent_and_guardian_is_visible(self):
        parent='00000000-0000-0000-0000-000000000001';child='00000000-0000-0000-0000-000000000002';internal='00000000-0000-0000-0000-000000000003'
        linked='00000000-0000-0000-0000-000000000004'
        items=[(parent,'user','cli',100),(child,'subagent','{"subagent":{"thread_spawn":{"parent_thread_id":"'+parent+'"}}}',40),(internal,'guardian_review','{"subagent":{"other":"guardian"}}',10),(linked,'guardian_review','{"subagent":{"other":"guardian"}}',10)]
        with closing(sqlite3.connect(self.root/'state_5.sqlite')) as db:
            db.execute('CREATE TABLE threads(id TEXT,title TEXT,cwd TEXT,rollout_path TEXT,source TEXT,thread_source TEXT)')
            db.execute('CREATE TABLE thread_spawn_edges(parent_thread_id TEXT,child_thread_id TEXT)')
            db.execute('INSERT INTO thread_spawn_edges VALUES (?,?)',(parent,child))
            for sid,kind,source,amount in items:
                meta=dict(id=sid,cwd='/work',source=source)
                if sid==linked:meta.update(parent_thread_id=parent,subagent_history_start_ordinal=2)
                entries=[dict(record('2026-10-12T02:00:00Z','session_meta',meta),ordinal=0)]
                if sid==linked:entries.append(dict(token('2026-10-12T02:00:00Z','inherited',100000,0,10),ordinal=1))
                entries += [dict(user('2026-10-12T02:00:01Z','u-'+sid,'要求'),ordinal=2),dict(token('2026-10-12T02:00:20Z','r-'+sid,amount,0,10,thread=sid),ordinal=3)]
                path=self.write('sessions/rollout-'+sid+'.jsonl',entries)
                db.execute('INSERT INTO threads VALUES (?,?,?,?,?,?)',(sid,sid,'/work',str(path),source,kind))
            db.commit()
        result=stats.collect(self.root,self.start,self.end)
        self.assertEqual(len(result['sessions']),2)
        main=next(s for s in result['sessions'] if not s['internal'])
        self.assertEqual(render.total_usage(main)['total_tokens'],180)
        self.assertEqual(len(main['sources']),3)
        self.assertIn('內部審核',[s['kind'] for s in main['sources']])
        self.assertEqual(next(s for s in result['sessions'] if s['internal'])['user_turns'],[])
    def test_injected_legacy_context_is_not_a_human_request(self):
        path=self.write('legacy.jsonl',[record('2026-10-12T02:00:00Z','response_item',dict(type='message',role='user',content=[dict(type='input_text',text='<environment_context>system</environment_context>')])),record('2026-10-12T02:00:01Z','response_item',dict(type='message',role='user',content=[dict(type='input_text',text='真正要求')]))])
        self.assertEqual([t['text'] for t in stats.read_rollout(path,self.start,self.end,[])['user_turns']],['真正要求'])
    def test_native_heartbeat_is_not_a_human_request(self):
        path=self.write('heartbeat.jsonl',[
            record('2026-10-12T02:00:00Z','event_msg',dict(type='task_started',turn_id='auto',turn_attribution=dict(turn_trigger='automation_heartbeat_scheduled'))),
            user('2026-10-12T02:00:01Z','a','自動檢查提示','auto'),
            token('2026-10-12T02:00:10Z','auto-response',10,5,2),
            record('2026-10-12T03:00:00Z','event_msg',dict(type='task_started',turn_id='manual',turn_attribution=dict(turn_trigger='composer'))),
            user('2026-10-12T03:00:01Z','b','<heartbeat>使用者親自貼上的文字</heartbeat>','manual')])
        parsed=stats.read_rollout(path,self.start,self.end,[])
        self.assertEqual(len(parsed['user_turns']),1)
        self.assertEqual(parsed['automated_inputs'],1)
        self.assertTrue(parsed['in_progress'])
        self.assertEqual(sum(x['total_tokens'] for x in parsed['usage'].values()),12)
    def test_quota_uses_bucket_window_and_preserves_null(self):
        limits={'rateLimits':{'primary':{'usedPercent':99}},'rateLimitsByLimitId':{'codex':{'primary':dict(usedPercent=75,windowDurationMins=10080,resetsAt=1792295903),'secondary':dict(usedPercent=None,windowDurationMins=300,resetsAt=None)}}}
        result=usage_json.normalize({'content':[dict(type='text',text=json.dumps(limits))]},'2026-10-12T10:00:00Z')
        self.assertEqual(result['windows'][0]['remaining_percent'],25)
        self.assertIn('7 天',result['windows'][0]['label'])
        self.assertIsNone(result['windows'][1]['remaining_percent'])
    def test_renderer_escapes_transcript_and_checks_stale_summary(self):
        turn=dict(key='u',turn_id='turn',timestamp=self.start.isoformat(),text='<script src="https://bad">boom</script>\n原文',deliveries=['完成'],in_progress=True,carried_in=False,fingerprint='turn-version')
        session=dict(key='thread',title='<img src=x onerror=bad>',cwd='/work',link='codex://threads/thread',internal=False,user_turns=[turn],delivery_evidence=['完成'],fingerprint='session-version',sources=[])
        data=dict(start=self.start.isoformat(),end=self.end.isoformat(),collected_at=self.end.isoformat(),host='TEST',sessions=[session],warnings=[],scope='test')
        summary={'thread':dict(request='要求',delivery='交付',fingerprint='session-version',turns={'u':dict(delivery='已完成部分',fingerprint='turn-version')})}
        text,gaps=render.render(data,summary)
        self.assertEqual(gaps,[]);self.assertEqual(selfcheck.validate(text),[])
        self.assertNotIn('<script src=',text);self.assertIn('&lt;script',text)
        summary['thread']['turns']['u']['fingerprint']='old';self.assertTrue(render.summary_gaps(data,summary))
        stats.atomic_json(self.root/'stats.json',data);stats.atomic_json(self.root/'summaries.json',summary)
        report=self.root/'report.html';report.write_text('existing complete report',encoding='utf-8')
        run=subprocess.run([sys.executable,str(SCRIPTS/'worklog_render.py'),str(self.root/'stats.json'),str(self.root/'summaries.json'),str(report)],capture_output=True)
        self.assertEqual(run.returncode,2);self.assertEqual(report.read_text(),'existing complete report')
        self.assertTrue((self.root/'report.draft.html').exists())
    def test_end_is_exclusive_and_partial_record_warns(self):
        path=self.write('partial.jsonl',[token('2026-10-12T16:00:00Z','outside',10,0,1)])
        with path.open('a',encoding='utf-8') as stream:stream.write('{')
        warnings=[];self.assertEqual(stats.read_rollout(path,self.start,self.end,warnings)['usage'],{});self.assertTrue(warnings)

    def test_default_week_and_explicit_cutoff_across_year(self):
        tz=stats.fixed_tz('+08:00');now=stats.parse_time('2026-10-14T05:00:00+08:00')
        start,end=week.week_bounds(tz,now=now)
        self.assertEqual(start.isoformat(),'2026-10-07T00:00:00+08:00')
        self.assertEqual(end.isoformat(),'2026-10-14T00:00:00+08:00')
        self.assertEqual(week.week_bounds(tz,'2026-10-13',now),(start,end))
        start,end=week.week_bounds(tz,'2025-12-31',now)
        self.assertEqual(start.date().isoformat(),'2025-12-25')
        self.assertEqual(end.date().isoformat(),'2026-01-01')
        with self.assertRaises(ValueError):week.week_bounds(tz,'2026-10-14',now)

    def test_week_carry_replay_daily_totals_and_render(self):
        start=stats.parse_time('2026-10-07T00:00:00+08:00');end=stats.parse_time('2026-10-14T00:00:00+08:00')
        sid='00000000-0000-0000-0000-000000000001'
        self.write('sessions/rollout-'+sid+'.jsonl',[
            record('2026-10-06T14:00:00Z','session_meta',dict(id=sid)),
            user('2026-10-06T15:00:00Z','carry','前一週要求'),
            token('2026-10-06T15:59:59Z','outside',999,0,1),
            token('2026-10-06T16:00:00Z','first',10,5,2),
            token('2026-10-07T16:00:00Z','first',10,5,2),
            user('2026-10-13T15:59:00Z','last-user','本週要求'),
            token('2026-10-13T15:59:59Z','last',20,10,3),
            token('2026-10-13T16:00:00Z','after',999,0,1)])
        result=week.collect_week(self.root,start,end)
        self.assertEqual(len(result['daily']),7)
        self.assertEqual(result['daily']['2026-10-07']['usage']['total_tokens'],12)
        self.assertEqual(result['daily']['2026-10-08']['usage']['total_tokens'],0)
        self.assertEqual(result['daily']['2026-10-13']['usage']['total_tokens'],23)
        self.assertEqual(sum(d['human_inputs'] for d in result['daily'].values()),1)
        session=result['sessions'][0]
        self.assertEqual(render.total_usage(session)['total_tokens'],35)
        summary={sid:dict(request='要求',delivery='部分完成',fingerprint=session['fingerprint'],turns={t['key']:dict(delivery='依來源',fingerprint=t['fingerprint']) for t in session['user_turns']})}
        text,gaps=render.render(result,summary)
        self.assertEqual(gaps,[]);self.assertEqual(selfcheck.validate(text),[])
        self.assertIn('Codex 工作週報 · 2026-10-07–2026-10-13',text)
        summary[sid]['fingerprint']='outdated'
        self.assertTrue(render.render(result,summary)[1])


if __name__=='__main__':
    unittest.main()
