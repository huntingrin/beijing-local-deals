#!/usr/bin/env python3
"""Initialize searches and render evidence-labelled QR reports. Does not search or claim."""
import argparse, base64, hashlib, html, json, os, shutil, subprocess, sys
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlsplit
import jsonschema, qrcode, zxingcpp
from PIL import Image, ImageDraw, ImageFont
from common import now, stamp, public_url, emit

ROOT=Path(__file__).resolve().parent.parent
LABELS={'article':'文章入口码','activity':'活动入口码','mini_program':'原始小程序码'}
RULES={'official_checked':'官方规则已核实','article_reported':'据原文；官方规则未独立核实','unknown':'规则未核实'}
ENTRIES={'page_checked':'对应页面已核对；微信打开未确认','user_confirmed':'用户已确认打开','unverified':'微信打开待确认'}
AFFILIATE={'yes':'第三方佣金推广','no':'已核对，无佣金推广','unknown':'佣金关系未知'}

def dump(path,data):
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')

def validate(data, *, historical=False):
    schema=json.loads((ROOT/'references/output-schema.json').read_text(encoding='utf-8'))
    jsonschema.Draft202012Validator(schema,format_checker=jsonschema.FormatChecker()).validate(data)
    checked=stamp(data['checked_at']); today=now().date(); target=date.fromisoformat(data['target_date'])
    if not historical and (checked.date()!=today or target<today):
        raise ValueError('需要本次检查和当前/未来目标日期；旧记录仅可用 --historical 回放')
    if checked>now()+timedelta(minutes=5): raise ValueError('检查时间不能在未来')
    ids=set()
    if any(v['detail'].startswith('尚未运行') for v in data['channels'].values()):
        raise ValueError('交付前必须填写渠道的实际检查结果')
    for o in data['offers']:
        if o['id'] in ids: raise ValueError('二维码编号重复')
        ids.add(o['id']); v=o['verification']; e=o['entry']
        if not date.fromisoformat(o['valid_from'])<=target<=date.fromisoformat(o['valid_until']):
            raise ValueError(o['id']+': 活动期不匹配，移入leads')
        if o['claim_by'] and date.fromisoformat(o['claim_by'])<checked.date():
            raise ValueError(o['id']+': 领取截止已过，移入leads')
        if v['rules']=='unknown': raise ValueError(o['id']+': 规则未核实，移入leads')
        if not any(s['body_read'] for s in o['sources']): raise ValueError('只有摘要不能生成优惠码')
        if v['rules']=='official_checked' and not any(s['role']=='official' and s['body_read'] for s in o['sources']):
            raise ValueError('官方规则状态缺少已读官方证据')
        for k in ('entry','claim'):
            if v[k]=='user_confirmed' and not v.get(k+'_evidence'): raise ValueError('用户确认缺少对应证据: '+k)
        for s in o['sources']:
            public_url(s['url']); t=stamp(s['checked_at'])
            if t>checked+timedelta(minutes=5): raise ValueError('来源检查时间晚于报告')
            if t.date()!=checked.date(): raise ValueError('来源未经报告当日复核，不能刷新时间伪装新结果')
        if e['kind']=='article':
            if not e['url'] or urlsplit(e['url']).hostname!='mp.weixin.qq.com' or not urlsplit(e['url']).path.startswith('/s'):
                raise ValueError('文章入口必须为微信原文章地址')
            if not any(s['url']==e['url'] and s['body_read'] for s in o['sources']):
                raise ValueError('文章入口须对应实际读过的原文')
        if e['kind']=='mini_program':
            if not e['image'] or not e['image_reviewed']: raise ValueError('小程序码需要已目视核对的原图')
        elif not e['url']:
            raise ValueError('普通二维码需要原始URL')
        if e['url']: public_url(e['url'])
        if e['image'] and not e['image_reviewed']: raise ValueError('原图需先目视核对')
    return data

def init_run(request,out):
    out=Path(out); out.mkdir(parents=True,exist_ok=False); n=now(); month=n.strftime('%Y年%m月')
    queries={
      'web':[f'北京 {request} 优惠 {month}',f'北京 {request} 官方 活动 领券'],
      'xiaohongshu':[f'北京 {request} 优惠',f'北京 {request} {month}'],
      'weixin':[f'北京 {request} {month}',f'北京 {request} 福利 领券']}
    dump(out/'search-plan.json',{'request':request,'created_at':n.isoformat(),'recent_since':(n-timedelta(days=30)).date().isoformat(),'queries':queries,'note':'由Agent运行可用渠道；不得将本计划当成已搜索结果'})
    dump(out/'offers.json',{'request':request,'checked_at':n.isoformat(),'target_date':n.date().isoformat(),'channels':{k:{'status':'not_needed','detail':'尚未运行；交付前必须更新为实际状态'} for k in queries},'offers':[],'leads':[]})
    emit(str(out.resolve()/'search-plan.json'))

def qr_for(o,base,out):
    e=o['entry']; payload=e.get('url'); prefix=o['id']+'-'+e['kind']; digest=None
    if e.get('image'):
        src=(base/e['image']).resolve()
        with Image.open(src) as im:
            if im.format not in ('PNG','JPEG') or max(im.size)>2400: raise ValueError('原码图必须为PNG/JPEG且边长不超过2400；请另存原始小码')
            ext='.png' if im.format=='PNG' else '.jpg'
            detected=[b.text for b in zxingcpp.read_barcodes(im)]
        if e['kind']!='mini_program' and payload not in detected: raise ValueError('原二维码解码与目标不一致')
        for value in detected: public_url(value.strip())
        dest=out/(prefix+ext); shutil.copyfile(src,dest)
        digest=hashlib.sha256(src.read_bytes()).hexdigest()
        verification='original_minicode_not_decoded' if e['kind']=='mini_program' else 'decoded_matches_url'
    else:
        dest=out/(prefix+'.png'); q=qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M,box_size=8,border=4)
        q.add_data(payload); q.make(fit=True); q.make_image(fill_color='black',back_color='white').save(dest)
        with Image.open(dest) as im: result=zxingcpp.read_barcodes(im)
        if payload not in [r.text for r in result]: raise ValueError('生成二维码反解不一致')
        verification='generated_roundtrip_verified'
    return dest,{'qr_file':dest.name,'qr_check':verification,'original_sha256':digest,'sha256':hashlib.sha256(dest.read_bytes()).hexdigest()}

def describe(o,historical):
    e=o['entry'];v=o['verification']
    lines=(["历史回放 / 示例，非当前推荐"] if historical else [])+[
     f"{o['id']}｜{o['title']}｜{LABELS[e['kind']]}",
     '优惠：'+o['benefit'],'区域：'+o['area'],
     '活动使用期：'+o['valid_from']+' 至 '+o['valid_until'],
     '领取截止：'+(o['claim_by'] or '来源未明确/无需领券，见具体条件'),
     '条件：'+'；'.join(o['conditions']),
     '扫码后：'+' → '.join(o['steps']),
     '规则：'+RULES[v['rules']], '入口：'+ENTRIES[v['entry']],
     '领取：'+('用户确认领取成功' if v['claim']=='user_confirmed' else '未尝试领取'),
     '核验说明：'+v['notes'], '入口说明：'+e['notes'],
     '推广：'+AFFILIATE[o['affiliate']],
     '来源：'+'、'.join(s['name'] for s in o['sources'])]
    return lines

def find_font(spec=None):
    choices=[spec] if spec else []
    choices += ['/System/Library/Fonts/PingFang.ttc','/System/Library/Fonts/STHeiti Light.ttc','C:/Windows/Fonts/msyh.ttc','/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc','/usr/share/fonts/truetype/wqy/wqy-microhei.ttc']
    return next((x for x in choices if x and Path(x).is_file()),None)

def card(qr,lines,dest,font_path):
    # Report composition; original QR file is preserved byte-for-byte separately.
    font=ImageFont.truetype(font_path,26)
    with Image.open(qr) as original: im=original.convert('RGB')
    w=max(1040,im.width+80); wrapped=[]
    for line in lines:
        current=''
        for c in line:
            if font.getlength(current+c)>w-80:
                wrapped.append(current);current=c
            else:current+=c
        wrapped.extend([current,''])
    height=im.height+100+len(wrapped)*36
    canvas=Image.new('RGB',(w,height),'white');canvas.paste(im,((w-im.width)//2,30));draw=ImageDraw.Draw(canvas)
    y=im.height+65
    for line in wrapped: draw.text((40,y),line,font=font,fill='#182437');y+=36
    canvas.save(dest)
    # Ordinary generated cards must remain machine-readable, not only attractive.
    if zxingcpp.read_barcodes(im):
        before={x.text for x in zxingcpp.read_barcodes(im)}
        after={x.text for x in zxingcpp.read_barcodes(canvas)}
        if not before.issubset(after): raise ValueError('说明卡二维码反解失败')

def open_files(files):
    if sys.platform=='darwin': subprocess.run(['open',*[str(p.resolve()) for p in files]],check=True)
    elif os.name=='nt':
        for p in files: os.startfile(str(p.resolve()))
    elif os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY'):
        for p in files: subprocess.run(['xdg-open',str(p.resolve())],check=True)
    else: raise RuntimeError('当前没有本地GUI；请下载交付目录里的图片，不能声称已打开')

def build(input_path,out,*,historical=False,show=False,font=None):
    input_path=Path(input_path).resolve();data=json.loads(input_path.read_text(encoding='utf-8'))
    if data.get('example') and not historical: raise ValueError('示例仅可用 --historical 构建，不可当成当前优惠')
    validate(data,historical=historical)
    out=Path(out).resolve();out.mkdir(parents=True,exist_ok=False)
    font_path=find_font(font); manifests=[];pages=[];display=[];summaries=[]
    esc=html.escape
    for o in data['offers']:
        qr,receipt=qr_for(o,input_path.parent,out)
        if o['entry'].get('image'):
            o['entry']['image']=qr.name
        lines=describe(o,historical)+['检查时间：'+data['checked_at']]
        text=out/(o['id']+'-说明.txt');text.write_text('\n'.join(lines+['','检查时间：'+data['checked_at']]+[s['url'] for s in o['sources']]),encoding='utf-8')
        shown=qr
        if font_path:
            shown=out/(o['id']+'-说明卡.png');card(qr,lines,shown,font_path)
        receipt.update(id=o['id'],kind=o['entry']['kind'],display_file=shown.name);manifests.append(receipt);display.append(shown)
        encoded=base64.b64encode(qr.read_bytes()).decode();mime='image/png' if qr.suffix=='.png' else 'image/jpeg'
        source_links=' '.join(f'<a href="{esc(s["url"],quote=True)}" rel="noreferrer">{esc(s["name"])}</a>' for s in o['sources'])
        pages.append('<article><img alt="'+esc(LABELS[o['entry']['kind']])+'" src="data:'+mime+';base64,'+encoded+'"><div>'+''.join('<p>'+esc(line)+'</p>' for line in lines)+'<p>'+source_links+'</p></div></article>')
        summaries.append('\n'.join(lines))
    channels='；'.join(k+': '+v['status']+' ('+v['detail']+')' for k,v in data['channels'].items())
    title=('历史回放｜' if historical else '')+'北京吃喝玩乐优惠'
    leads=''.join('<li>'+esc(x['title']+'：'+x['reason'])+'</li>' for x in data['leads'])
    report='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+title+'</title><style>body{font:16px/1.65 system-ui,sans-serif;max-width:1150px;margin:30px auto;padding:0 20px;color:#182437;background:#f3f6f9}article{display:flex;gap:24px;align-items:flex-start;background:white;padding:24px;margin:24px 0;border-radius:14px}img{width:300px;max-width:100%;height:auto}p{margin:8px 0}a{color:#145da0}@media(max-width:750px){article{display:block}}</style><h1>'+title+'</h1><p>'+esc(data['request'])+'</p><p>检查时间：'+esc(data['checked_at'])+'；使用日期：'+esc(data['target_date'])+'</p><p>'+esc(channels)+'</p>'+''.join(pages)+'<h2>未纳入扫码推荐的线索</h2><ul>'+leads+'</ul></html>'
    (out/'index.html').write_text(report,encoding='utf-8');(out/'说明.txt').write_text(title+'\n'+channels+'\n\n'+'\n\n'.join(summaries),encoding='utf-8')
    dump(out/'offers.json',data);dump(out/'manifest.json',{'generated_at':now().isoformat(),'historical':historical,'entries':len(manifests),'distinct_activities':len({o['activity_id'] for o in data['offers']}),'font_cards':bool(font_path),'files':manifests})
    if show and display: open_files(display)
    emit(json.dumps({'report':str(out/'index.html'),'entries':len(manifests),'distinct_activities':len({o['activity_id'] for o in data['offers']}),'opened':bool(show and display),'images':[str(x) for x in display]},ensure_ascii=False))
    return manifests

def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='cmd',required=True)
    i=sub.add_parser('init');i.add_argument('--request',required=True);i.add_argument('--out',required=True)
    b=sub.add_parser('build');b.add_argument('--input',required=True);b.add_argument('--out',required=True);b.add_argument('--historical',action='store_true');b.add_argument('--open',action='store_true');b.add_argument('--font')
    a=p.parse_args()
    try:
        if a.cmd=='init':init_run(a.request,a.out)
        else:build(a.input,a.out,historical=a.historical,show=a.open,font=a.font)
    except (ValueError,RuntimeError,jsonschema.ValidationError,OSError) as e:
        emit('未完成：'+str(e),file=sys.stderr);raise SystemExit(2)

if __name__=='__main__':main()
