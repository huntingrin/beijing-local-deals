#!/usr/bin/env python3
"""Bounded Sogou/WeChat adapter. Parser portions adapted from wx-search-cli (MIT)."""
import argparse, hashlib, http.cookiejar, json, os, re, time
from pathlib import Path
from urllib.request import build_opener, HTTPCookieProcessor, HTTPRedirectHandler, Request
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlsplit
from datetime import datetime, timezone
from bs4 import BeautifulSoup
from common import now, public_url, emit

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36 Edg/137.0.0.0'
HOSTS = {'weixin.sogou.com', 'mp.weixin.qq.com'}

def state_dir():
    return Path(os.environ.get('BEIJING_DEALS_STATE_DIR', str(Path.home()/'.local/state/beijing-local-deals')))

def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

def stop(reason):
    p = state_dir(); p.mkdir(parents=True, exist_ok=True)
    f = p/'weixin-stop.json'
    if not f.exists():
        write_json(f, {'state':'blocked', 'at':now().isoformat(), 'reason':reason, 'resume':'仅在用户正常完成验证并确认后归档此记录；不自动清除'})
    return f

def classify(html, url='', status=200):
    low = (url + '\n' + html).lower()
    if any(x in low for x in ('antispider', 'seccoderight', 'anti.min.css', 'wappoc_appmsgcaptcha', '/mp/verify')):
        return 'blocked'
    s = BeautifulSoup(html, 'html.parser')
    if not s.select_one('#js_content') and 'captcha.gtimg.com/tcaptcha.js' in low:
        return 'blocked'
    if status in (403, 429):
        return 'blocked'
    if not 200 <= status < 300:
        return 'http_error'
    if any(x in s.get_text() for x in ('该内容已被发布者删除','此内容因违规无法查看','该内容已被删除')):
        return 'unavailable'
    return 'ok'

def parse_search(html, url='https://weixin.sogou.com', status=200):
    state = classify(html, url, status)
    if state != 'ok': return {'state':state, 'items':[]}
    s = BeautifulSoup(html, 'html.parser'); items=[]
    for row in s.select('li[id*="sogou_vr_11002601_box_"]'):
        a = row.select_one('a[id*="sogou_vr_11002601_title_"]'); t = row.select_one('div.txt-box > div.s-p > span.s2')
        if not a or not t or not a.get('href'): return {'state':'parse_error','items':items,'reason':'missing_search_metadata'}
        raw=t.decode_contents(); m=re.search(r"timeConvert\('(\d+)'\)", raw)
        date=datetime.fromtimestamp(int(m[1]), timezone.utc).isoformat() if m else t.get_text(strip=True)
        account=row.select_one('.all-time-y2')
        items.append({'title':a.get_text(' ',strip=True),'link':urljoin('https://weixin.sogou.com',a['href']),'publish_time':date,'account':account.get_text(strip=True) if account else None,'real_url':None})
    if len(s.select('a[id*="sogou_vr_11002601_title_"]')) != len(items):
        return {'state':'parse_error','items':items,'reason':'incomplete_search_metadata'}
    if not items:
        return {'state':'empty_results' if re.search('没有找到|未找到|无相关结果',s.get_text()) else 'parse_error','items':[]}
    return {'state':'search_ok','items':items}

def parse_redirect(html):
    parts=re.findall(r"url \+= '([^']*)'",html)
    value=''.join(parts).replace('@','')
    if not value: return None
    value=value if value.startswith('http') else 'https://mp.'+value
    public_url(value)
    if urlsplit(value).hostname != 'mp.weixin.qq.com': raise ValueError('非微信原文地址')
    return value

def parse_article(html, url='', status=200):
    state=classify(html,url,status)
    if state!='ok': return {'state':state,'body_read':False}
    s=BeautifulSoup(html,'html.parser'); body=s.select_one('#js_content')
    if body is None: return {'state':'parse_error','body_read':False,'reason':'missing_js_content'}
    for el in body.select('script,style'): el.decompose()
    text=body.get_text('\n',strip=True)
    title=s.select_one('#activity-name'); account=s.select_one('#js_name')
    m=re.search(r'create_time\s*[:=]\s*[\'"]?(\d{10})',html)
    imgs=[{'url':i.get('data-src') or i.get('src'),'context':i.parent.get_text(' ',strip=True)[:400]} for i in body.select('img')]
    return {'state':'article_ok' if text else ('media_only' if imgs else 'empty_body'),'body_read':bool(text),'title':title.get_text(strip=True) if title else None,'account':account.get_text(strip=True) if account else None,'original_url':url,'published_at':datetime.fromtimestamp(int(m[1]),timezone.utc).isoformat() if m else None,'body_text':text,'links':[{'text':a.get_text(' ',strip=True),'url':urljoin(url,a['href'])} for a in body.select('a[href]')],'images':imgs,'mini_programs':[dict(x.attrs) for x in body.select('mp-miniprogram,[data-miniprogram-appid]')],'verification':{'rules':'unknown','entry':'unverified','claim':'not_attempted'}}

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl): return None

class Session:
    def __init__(self, out):
        self.out=Path(out); self.out.mkdir(parents=True, exist_ok=False)
        self.opener=build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()), NoRedirect())
        self.n=0; self.last=0; self.search_cookies=''; self.referer=''
    def get(self, url):
        if (state_dir()/'weixin-stop.json').exists(): raise RuntimeError('blocked: prior_verification_stop')
        for _ in range(6):
            if classify('',url)=='blocked':
                stop('验证跳转已检测；未请求验证页'); raise RuntimeError('blocked: verification_redirect')
            public_url(url,allow_sogou_token=True)
            if urlsplit(url).hostname not in HOSTS: raise ValueError('host_not_allowed')
            time.sleep(max(0,3-(time.monotonic()-self.last))); self.last=time.monotonic()
            headers={'User-Agent':UA,'Accept':'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7','Accept-Language':'zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6','Cache-Control':'no-cache','Connection':'keep-alive','Pragma':'no-cache'}
            if urlsplit(url).hostname=='weixin.sogou.com':
                headers['Referer']=self.referer or 'https://weixin.sogou.com/'
                if self.search_cookies: headers['Cookie']=self.search_cookies
            try: r=self.opener.open(Request(url,headers=headers),timeout=20)
            except HTTPError as e: r=e
            except (URLError,TimeoutError) as e: raise RuntimeError('network_error: '+type(e).__name__) from e
            with r:
                data=r.read(8_000_001)
                if len(data)>8_000_000: raise RuntimeError('response_too_large')
                html=data.decode('utf-8','replace'); status=r.code; location=r.headers.get('Location')
                if urlsplit(url).hostname=='weixin.sogou.com' and urlsplit(url).path=='/weixin':
                    self.search_cookies='; '.join(x.split(';')[0] for x in r.headers.get_all('Set-Cookie',[])); self.referer=url
            self.n+=1; stem=self.out/f'{self.n:02}'
            stem.with_suffix('.html').write_bytes(data)
            state=classify(html,url,status)
            write_json(stem.with_suffix('.json'),{'at':now().isoformat(),'url':url,'status':status,'state':state,'sha256':hashlib.sha256(data).hexdigest()})
            if state=='blocked': stop('验证或限流响应；证据保存在当前运行目录'); raise RuntimeError('blocked: verification_required')
            if 300<=status<400 and location: url=urljoin(url,location); continue
            return html,url,status
        raise RuntimeError('redirect_limit')

def main():
    a=argparse.ArgumentParser(description=__doc__); sub=a.add_subparsers(dest='cmd',required=True)
    for name in ('search','article'):
        p=sub.add_parser(name); p.add_argument('value'); p.add_argument('--out',required=True)
        if name=='search': p.add_argument('--limit',type=int,default=3,choices=range(1,11))
    p=sub.add_parser('stop'); p.add_argument('--reason',required=True)
    p=sub.add_parser('parse'); p.add_argument('--kind',choices=['search','article'],required=True); p.add_argument('--html',required=True); p.add_argument('--url',default=''); p.add_argument('--out',required=True)
    args=a.parse_args()
    if args.cmd=='stop': emit(stop(args.reason)); return
    if args.cmd=='parse':
        result=(parse_search if args.kind=='search' else parse_article)(Path(args.html).read_text(encoding='utf-8'),args.url)
        write_json(Path(args.out),result); emit(result['state']); return
    # A previous block is checked before creating an output directory or any request.
    if (state_dir()/'weixin-stop.json').exists():
        emit(json.dumps({'state':'blocked','reason':'prior_verification_stop','network_requests':0})); raise SystemExit(2)
    session=Session(args.out); result={'state':'started','items':[],'checked_at':now().isoformat()}
    try:
        if args.cmd=='article':
            public_url(args.value)
            if urlsplit(args.value).hostname!='mp.weixin.qq.com': raise ValueError('需要微信原文URL')
            result=parse_article(*session.get(args.value))
        else:
            url='https://weixin.sogou.com/weixin?'+urlencode({'type':'2','s_from':'input','query':args.value,'ie':'utf8','page':'1','_sug_':'n','_sug_type_':''})
            session.referer='https://weixin.sogou.com/weixin?'+urlencode({'query':args.value})
            result=parse_search(*session.get(url)); result['query']=args.value
            write_json(session.out/'result.json',result)
            if result['state']=='search_ok':
                for item in result['items'][:args.limit]:
                    html,final,status=session.get(item['link'])
                    if classify(html,final,status)!='ok': item['resolution']='http_error'; continue
                    item['real_url']=final if urlsplit(final).hostname=='mp.weixin.qq.com' else parse_redirect(html)
                    item['resolution']='resolved' if item['real_url'] else 'parse_error'
                    write_json(session.out/'result.json',result)
        if result['state']=='blocked': stop('解析发现验证页')
    except (ValueError,RuntimeError) as e:
        result.update(state='blocked' if str(e).startswith('blocked:') else 'error',reason=str(e))
    result['checked_at']=now().isoformat(); write_json(session.out/'result.json',result)
    emit(json.dumps({'state':result['state'],'result':str(session.out/'result.json'),'requests':session.n},ensure_ascii=False))
    if result['state'] not in ('search_ok','article_ok','media_only','empty_results'): raise SystemExit(2)

if __name__=='__main__': main()
