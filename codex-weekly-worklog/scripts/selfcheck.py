"""Verify a single-file worklog: resources, anchors, escaped prose, chart, sections."""
import argparse
import re
import sys
from html.parser import HTMLParser
from pathlib import Path


class Check(HTMLParser):
    def __init__(self):
        super().__init__();self.ids=set();self.anchors=[];self.errors=[];self.svg=False
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if 'id' in attrs:
            if attrs['id'] in self.ids:self.errors.append('duplicate id')
            self.ids.add(attrs['id'])
        if tag=='svg':self.svg=True
        if tag in ('script','iframe','object','embed','link'):
            self.errors.append('外部或可執行資源：'+tag)
        for key,value in attrs.items():
            if key.startswith('on'):self.errors.append('inline event handler')
            if key in ('src','srcset','poster','data') and value and not value.startswith('data:'):
                self.errors.append('非嵌入資源')
            if tag=='a' and key=='href':
                if value.startswith('#'):self.anchors.append(value[1:])
                elif not value.startswith(('https://','codex://threads/')):self.errors.append('不允許的連結協定')


def validate(text):
    check=Check();check.feed(text)
    check.errors+=['缺少章節 '+k for k in ('overview','quota','details','limits') if k not in check.ids]
    check.errors+=['失效錨點 '+k for k in check.anchors if k not in check.ids]
    if not check.svg:check.errors.append('缺少資料圖表')
    if '__TITLE__' in text or '__BODY__' in text:check.errors.append('殘留模板占位符')
    style=''.join(re.findall(r'<style[^>]*>(.*?)</style>',text,re.S|re.I))
    if re.search(r'@import|url\(',style,re.I):check.errors.append('CSS 載入資源')
    return check.errors


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8');parser=argparse.ArgumentParser();parser.add_argument('report',type=Path);args=parser.parse_args();errors=validate(args.report.read_text(encoding='utf-8'))
    print('\n'.join(errors) if errors else 'PASS：單檔、圖表、章節、錨點與 HTML 資源驗證通過');sys.exit(bool(errors))
