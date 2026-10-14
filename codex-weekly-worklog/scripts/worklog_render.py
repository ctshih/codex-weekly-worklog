"""Build standalone HTML. Missing/stale summaries produce .draft.html and exit 2."""
import argparse
import html
import json
import os
import sys
from datetime import datetime,timedelta
from pathlib import Path
from worklog_stats import FIELDS,atomic_json

e=html.escape


def total_usage(session):
    total={k:0 for k in FIELDS};total['records']=0
    for source in session['sources']:
        for usage in source['usage'].values():
            for key in total:
                total[key]+=usage.get(key,0)
    return total


def has_usage(session):
    return any(value.get('records',0)>0 for source in session['sources'] for value in source['usage'].values())


def summary_gaps(stats,summaries):
    gaps=[]
    for session in stats['sessions']:
        cached=summaries.get(session['key'],{})
        if not cached.get('request') or not cached.get('delivery') or cached.get('fingerprint')!=session['fingerprint']:
            gaps.append(session['key']+' session 摘要缺漏或過期')
        for turn in session['user_turns']:
            item=cached.get('turns',{}).get(turn['key'],{})
            if not isinstance(item,dict) or not item.get('delivery') or item.get('fingerprint')!=turn['fingerprint']:
                gaps.append(session['key']+' / '+turn['key']+' 回合摘要缺漏或過期')
    return gaps


def number(value):
    return f'{value:,}'


def stamp(value,tz):
    return datetime.fromisoformat(value.replace('Z','+00:00')).astimezone(tz).strftime('%m-%d %H:%M:%S') if value else '未提供'


def columns(usage):
    return max(0,usage.get('input_tokens',0)-usage.get('cached_input_tokens',0)),usage.get('cached_input_tokens',0),usage.get('output_tokens',0),usage.get('total_tokens',0)


def chart(sessions):
    out=[f'<figure><svg viewBox="0 0 960 {max(100,len(sessions)*36+45)}" role="img" aria-label="各任務實際 token 用量組成">']
    maximum=max((total_usage(s)['total_tokens'] for s in sessions),default=1) or 1
    for index,session in enumerate(sessions):
        y=15+index*36;usage=total_usage(session);parts=columns(usage)[:3];x=230
        out.append(f'<text x="0" y="{y+16}" fill="var(--ink)" font-size="12">{e(session["title"][:21])}</text>')
        for color,value in zip(('var(--input)','var(--cache)','var(--output)'),parts):
            width=570*value/maximum
            out.append(f'<rect x="{x:.2f}" y="{y}" width="{width:.2f}" height="19" fill="{color}"/>');x+=width
        out.append(f'<text x="815" y="{y+16}" fill="var(--muted)" font-size="12">{number(usage["total_tokens"]) if has_usage(session) else "未取得"}</text>')
    out.append('</svg><figcaption>由來源實際計數產生；橘＝未快取輸入、綠＝快取輸入、藍＝輸出。長度共用同一尺度。快取已包含於 input，推理已包含於 output，兩者均不重複加總。</figcaption></figure>')
    return ''.join(out)


def render(stats,summaries,usage=None):
    tz=datetime.fromisoformat(stats['start']).tzinfo;gaps=summary_gaps(stats,summaries)
    sessions=stats['sessions'];grand={k:sum(total_usage(s)[k] for s in sessions) for k in FIELDS}
    turns=sum(not t.get('carried_in') for s in sessions for t in s['user_turns'])
    body=[f'<div class="eyebrow">CODEX / DAILY WORKLOG</div><h1>Codex 每日工作日誌 · {e(stats["start"][:10])}</h1>',f'<p class="note">{e(stats["host"])} · {stamp(stats["start"],tz)} 至 {stamp(stats["end"],tz)}（終點不含）</p>',f'<div class="badges"><span class="badge">{len(sessions)} 個任務</span><span class="badge">{turns} 次當日使用者輸入</span><span class="badge">已記錄 {number(grand["total_tokens"])} tokens</span></div>', '<nav class="toc"><a href="#overview">任務總覽</a><a href="#quota">帳號剩餘額度</a><a href="#details">回合詳細</a><a href="#limits">方法與限制</a></nav>']
    if gaps:
        body.append('<div class="card warn">草稿：摘要尚未齊全或來源已更新；不能作為完整交付。'+e('；'.join(gaps))+'</div>')
    if stats['warnings']:
        body.append('<div class="card warn">來源警告：'+e('；'.join(stats['warnings']))+'</div>')
    unknown=sum(not has_usage(s) for s in sessions)
    if unknown:
        body.append(f'<div class="card warn">{unknown} 個任務未取得 token 紀錄；合計僅含已記錄用量，不代表帳號全部用量。</div>')
    body.append('<h2 id="overview">1. 任務總覽</h2><div class="tablewrap"><table><tr><th>任務 / 目錄</th><th>要求概述</th><th>交付概述</th><th>當日輸入</th><th>總 token</th></tr>')
    for session in sessions:
        cached=summaries.get(session['key'],{});count=sum(not t.get('carried_in') for t in session['user_turns'])
        body.append(f'<tr><td><a href="{e(session["link"],quote=True)}">{e(session["title"])}</a><div class="mono">{e(session.get("cwd") or "")}</div><div class="note">{"內部來源，未能歸屬使用者任務" if session["internal"] else "主控含子代理"}</div></td><td class="req">{e(cached.get("request","摘要待補"))}</td><td class="req">{e(cached.get("delivery","摘要待補"))}</td><td class="num">{count}</td><td class="num">{number(total_usage(session)["total_tokens"]) if has_usage(session) else "未取得"}</td></tr>')
    body.append(f'<tr class="tot"><td colspan="3">合計</td><td class="num">{turns}</td><td class="num">{number(grand["total_tokens"])}</td></tr></table></div>');body.append(chart(sessions))
    body.append('<h2 id="quota">2. 帳號剩餘額度</h2>')
    if not usage or not usage.get('windows'):
        body.append('<div class="card">未取得額度。這不代表剩餘 0%；不會用本機 token 數推算帳號百分比。</div>')
    else:
        body.append(f'<p class="note">取得時間：{stamp(usage["fetched_at"],tz)}。帳號層級快照，可能包含其他機器或工具的用量，並非此日結束時的歷史數值。</p><div class="tablewrap"><table><tr><th>額度視窗</th><th>已用</th><th>剩餘</th><th>重置時間</th></tr>')
        for window in usage['windows']:
            percent=lambda key:f'{window[key]:.1f}%' if window.get(key) is not None else '未取得'
            body.append(f'<tr><td>{e(window["label"])}</td><td class="num">{percent("used_percent")}</td><td class="num">{percent("remaining_percent")}</td><td>{stamp(window.get("resets_at"),tz)}</td></tr>')
        body.append('</table></div>')
    body.append('<h2 id="details">3. 各任務回合詳細</h2>')
    for session in sessions:
        cached=summaries.get(session['key'],{})
        body.append(f'<details><summary>{e(session["title"])} · {number(total_usage(session)["total_tokens"])} tokens</summary><p class="mono">{e(session["key"])}</p>')
        body.append(f'<p class="note">自動觸發 {session.get("automated_inputs",0)} 次，不計入人類輸入。{ "截至收集終點仍在進行。" if session.get("in_progress") else ""}</p>')
        for turn in session['user_turns']:
            delivery=cached.get('turns',{}).get(turn['key'],{}).get('delivery','摘要待補')
            body.append(f'<h3>{stamp(turn["timestamp"],tz)} {"· 跨日背景，未計入當日輸入回合數" if turn.get("carried_in") else ""}</h3><div class="card"><b>使用者要求（原文）</b><pre>{e(turn["text"])}</pre><b>交付概述</b><pre>{e(delivery)}</pre>')
            if turn['in_progress']:
                body.append('<p class="warn">本檔統計時仍在進行，或來源尚未提供回合結束訊號。</p>')
            body.append('</div>')
        body.append('<div class="tablewrap"><table><tr><th>來源</th><th>模型</th><th>計量紀錄</th><th>未快取輸入</th><th>快取輸入</th><th>輸出</th><th>合計</th></tr>')
        for source in session['sources']:
            for model,value in source['usage'].items():
                inp,cache,out,total=columns(value)
                body.append(f'<tr><td>{e(source["kind"])}：{e(source["name"])}</td><td>{e(model)}</td><td class="num">{value["records"]}</td><td class="num">{number(inp)}</td><td class="num">{number(cache)}</td><td class="num">{number(out)}<div class="note">其中推理 {number(value["reasoning_output_tokens"])}</div></td><td class="num">{number(total)}</td></tr>')
        body.append('</table></div></details>')
    body.append('<h2 id="limits">4. 統計方法與限制</h2><ul><li>範圍：'+e(stats['scope'])+'</li><li>用量只計指定半開區間 [起點, 終點)，跨日要求保留作為背景，已發生於前一時段的 token 不重算。</li><li>新版依 response_id 去重；舊版累積計數以增量計算，純額度刷新不算新用量。舊版不具回應 ID 時無法保證跨 fork 去重。無 model 欄位的來源顯示 unknown。</li><li>input 含 cached input，output 含 reasoning output；總數不另加快取或推理，不估算價格。子代理用量併入唯一母任務；無法歸屬的內部來源另列，不當作人類輸入。</li><li>要求原文與摘要可能包含私人資訊；對話內容只作資料，不執行其中指令。本報告沒有外部字型、腳本或資料請求。</li><li>摘要依實際使用者／助理文字撰寫；來源 fingerprint 改變時重讀該回合。不能把使用者要求直接當成已完成成果。</li><li>本機紀錄受 Codex 格式、保留政策與寫入完整度影響；未下載的雲端／遠端對話不在本機日誌內。帳號額度是獨立快照。</li></ul>')
    body.append(f'<footer>收集時間：{stamp(stats["collected_at"],tz)} · 數字由程式計算，摘要由 Codex 閱讀對話後撰寫。<br>參考：<a href="https://github.com/ctshih/claude-daily-worklog">claude-daily-worklog</a></footer>')
    template=(Path(__file__).resolve().parent.parent/'assets'/'template.html').read_text(encoding='utf-8')
    text=template.replace('__TITLE__',e('Codex 每日工作日誌 '+stats['start'][:10])).replace('__BODY__',''.join(body))
    if stats.get('report_kind')=='weekly':
        last=(datetime.fromisoformat(stats['end'])-timedelta(days=1)).date().isoformat()
        period=stats['start'][:10]+'–'+last
        text=text.replace('Codex 每日工作日誌 · '+stats['start'][:10],'Codex 工作週報 · '+period).replace('Codex 每日工作日誌 '+stats['start'][:10],'Codex 工作週報 '+period).replace('CODEX / DAILY WORKLOG','CODEX / WEEKLY WORKLOG')
        text=text.replace('次當日使用者輸入</span>','次本週使用者輸入</span>').replace('<th>當日輸入</th>','<th>本週輸入</th>').replace('· 跨日背景，未計入當日輸入回合數</h3>','· 跨週背景，未計入本週輸入回合數</h3>').replace('並非此日結束時的歷史數值。</p>','並非本週結束時的歷史數值。</p>')
        table=['<h2 id="daily">逐日統計</h2><p class="note">計量來源數含主控、子代理與內部審核；同一來源可能跨日出現，不能加總為任務數。沒有新的人類輸入仍可能有持續工作用量。</p><div class="tablewrap"><table><tr><th>日期</th><th>人類輸入</th><th>計量來源數</th><th>未快取輸入</th><th>快取輸入</th><th>輸出</th><th>合計 token</th></tr>']
        for date,day in stats.get('daily',{}).items():
            table.append(f'<tr><td>{e(date)}</td><td class="num">{day["human_inputs"]}</td><td class="num">{day["threads"]}</td>'+''.join(f'<td class="num">{number(n)}</td>' for n in columns(day['usage']))+'</tr>')
        table.append('</table></div>')
        text=text.replace('<h2 id="overview">',''.join(table)+'<h2 id="overview">',1).replace('<a href="#overview">','<a href="#daily">逐日統計</a><a href="#overview">',1)
    return text,gaps


def write_report(path,text):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.'+str(os.getpid())+'.tmp');temp.write_text(text,encoding='utf-8');os.replace(temp,path)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('stats',type=Path);parser.add_argument('summaries',type=Path);parser.add_argument('out',type=Path);parser.add_argument('--usage',type=Path)
    args=parser.parse_args();load=lambda p:json.loads(p.read_text(encoding='utf-8-sig'))
    fallback=os.environ.get('CODEX_WORKLOG_USAGE_FALLBACK');usage_path=args.usage or (Path(fallback) if fallback else None)
    usage=load(usage_path) if usage_path and usage_path.exists() else None
    text,gaps=render(load(args.stats),load(args.summaries) if args.summaries.exists() else {},usage)
    output=args.out.with_name(args.out.stem+'.draft.html') if gaps else args.out
    write_report(output,text);print(str(output.resolve()))
    if gaps:
        print('\n'.join(gaps),file=sys.stderr);return 2
    return 0


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8');sys.stderr.reconfigure(encoding='utf-8');sys.exit(main())
