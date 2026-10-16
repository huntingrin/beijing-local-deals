import copy, json, os, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from datetime import timedelta
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from common import now, public_url
from deals import validate, build
from weixin import parse_search, parse_article, parse_redirect, classify, Session, stop

ROOT=Path(__file__).resolve().parents[1]

def fixture():
    d=json.loads((ROOT/'examples/offers.json').read_text(encoding='utf-8'));d.pop('example');n=now();d['checked_at']=n.isoformat();d['target_date']=n.date().isoformat();o=d['offers'][0];o['valid_from']=(n-timedelta(days=1)).date().isoformat();o['valid_until']=(n+timedelta(days=1)).date().isoformat();o['sources'][0]['checked_at']=n.isoformat();return d

class Pipeline(unittest.TestCase):
    def test_qr_and_report_roundtrip_and_html_escaping(self):
        d=fixture();d['offers'][0]['title']='<script>alert(1)</script>'
        with tempfile.TemporaryDirectory() as t:
            p=Path(t);(p/'input.json').write_text(json.dumps(d));m=build(p/'input.json',p/'out');self.assertEqual(m[0]['qr_check'],'generated_roundtrip_verified');h=(p/'out/index.html').read_text(encoding='utf-8');self.assertNotIn('<script>',h);self.assertIn('&lt;script&gt;',h)
    def test_expired_offer_rejected(self):
        d=fixture();d['offers'][0]['valid_until']=(now()-timedelta(days=1)).date().isoformat()
        with self.assertRaises(ValueError):validate(d)
    def test_future_target_after_claim_deadline(self):
        d=fixture();d['offers'][0]['claim_by']=(now()-timedelta(days=1)).date().isoformat()
        with self.assertRaises(ValueError):validate(d)
    def test_old_source_not_relabelled_current(self):
        d=fixture();d['offers'][0]['sources'][0]['checked_at']=(now()-timedelta(days=1)).isoformat()
        with self.assertRaises(ValueError):validate(d)
    def test_unsubstantiated_claim_rejected(self):
        d=fixture();d['offers'][0]['verification']['claim']='user_confirmed'
        with self.assertRaises(ValueError):validate(d)
    def test_article_requires_read_original(self):
        d=fixture();d['offers'][0]['entry'].update(kind='article',url='https://mp.weixin.qq.com/s/demo')
        with self.assertRaises(ValueError):validate(d)
        d['offers'][0]['sources'][0]['url']='https://mp.weixin.qq.com/s/demo';validate(d)
    def test_summary_is_not_body(self):
        d=fixture();d['offers'][0]['sources'][0]['body_read']=False
        with self.assertRaises(ValueError):validate(d)
    def test_private_or_session_url_rejected(self):
        for u in ['https://example.com/?access_token=abc','http://127.0.0.1/','https://example.com/?url=https%3A%2F%2Fx.com%2F%3Ftoken%3Dabc','https://u:p@example.com/']:
            with self.subTest(u=u),self.assertRaises(ValueError):public_url(u)
        public_url('https://mp.weixin.qq.com/s?timestamp=123&signature=publicsignature')
    def test_no_false_empty_success(self):
        self.assertEqual(parse_search('<html>something changed</html>')['state'],'parse_error')
        self.assertEqual(parse_search('<html>没有找到</html>')['state'],'empty_results')
    def test_search_metadata_and_redirect(self):
        h='''<li id="sogou_vr_11002601_box_0"><a id="sogou_vr_11002601_title_0" href="/link?url=abc">优惠</a><div class="txt-box"><div class="s-p"><span class="s2"><script>document.write(timeConvert('1792111111'))</script></span><a class="all-time-y2">账号</a></div></div></li>'''
        r=parse_search(h);self.assertEqual(r['state'],'search_ok');self.assertEqual(r['items'][0]['account'],'账号');self.assertTrue(r['items'][0]['publish_time'].startswith('2026'))
        self.assertEqual(parse_redirect("url += 'weixin.qq.com/s?';url += 'src=11';"),'https://mp.weixin.qq.com/s?src=11')
    def test_article_assets_and_no_claim_promotion(self):
        h='''<h1 id="activity-name">优惠</h1><span id="js_name">账号</span><div id="js_content"><p>北京满100减40</p><img data-src="https://example.com/code.png"><a href="https://example.com/offer">活动</a><mp-miniprogram data-miniprogram-appid="wx123"></mp-miniprogram></div><script>var create_time='1792111111';</script>'''
        r=parse_article(h,'https://mp.weixin.qq.com/s/demo');self.assertTrue(r['body_read']);self.assertEqual(len(r['images']),1);self.assertEqual(len(r['mini_programs']),1);self.assertEqual(r['verification']['claim'],'not_attempted')
    def test_http200_challenge_is_blocked(self):
        for h in ['<link href="anti.min.css">','<script src="https://captcha.gtimg.com/TCaptcha.js"></script>']:
            self.assertEqual(parse_article(h)['state'],'blocked')
    def test_sticky_stop_makes_no_network_call(self):
        with tempfile.TemporaryDirectory() as t,patch.dict(os.environ,{'BEIJING_DEALS_STATE_DIR':t}):
            stop('test');session=Session(Path(t)/'out')
            with patch.object(session.opener,'open',side_effect=AssertionError('network called')):
                with self.assertRaisesRegex(RuntimeError,'prior_verification_stop'):session.get('https://weixin.sogou.com/')
            self.assertEqual(session.n,0)
    def test_minicode_requires_review(self):
        d=fixture();o=d['offers'][0];o['entry'].update(kind='mini_program',url=None,image='original.png',image_reviewed=False)
        with self.assertRaises(ValueError):validate(d)
        # Proprietary codes cannot be decoded offline; agent review is explicitly required.
        o['entry']['image_reviewed']=True;validate(d)
    def test_original_image_preserved_and_delivery_portable(self):
        import qrcode
        d=fixture();o=d['offers'][0];o['entry'].update(image='original.png',image_reviewed=True)
        with tempfile.TemporaryDirectory() as t:
            p=Path(t);qrcode.make(o['entry']['url']).save(p/'original.png')
            original=(p/'original.png').read_bytes();(p/'input.json').write_text(json.dumps(d))
            m=build(p/'input.json',p/'out')
            self.assertEqual((p/'out'/m[0]['qr_file']).read_bytes(),original)
            built=json.loads((p/'out/offers.json').read_text(encoding='utf-8'))
            self.assertTrue((p/'out'/built['offers'][0]['entry']['image']).is_file())
            build(p/'out/offers.json',p/'rebuilt')
    def test_unexecuted_plan_is_not_a_result(self):
        d=fixture();d['channels']['web']['detail']='尚未运行；交付前更新'
        with self.assertRaises(ValueError):validate(d)

if __name__=='__main__':unittest.main()
