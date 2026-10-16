"""Shared URL and time validation; no network side effects."""
from datetime import datetime, timezone, timedelta
from urllib.parse import urlsplit, parse_qsl, unquote
import ipaddress, re, sys

BEIJING = timezone(timedelta(hours=8))
SENSITIVE = re.compile(r'^(?:access_?token|refresh_?token|auth_?token|session_?token|token|pass_ticket|wap_sid|wxtoken|wxuin|uin|key|sessionid|session|cookie|authorization|password|userid|user_id|openid|unionid|mobile|phone)$', re.I)

def now():
    return datetime.now(BEIJING)

def emit(value, *, file=None):
    """Keep UTF-8 artifacts intact when a Windows pipe only accepts ANSI text."""
    stream = file or sys.stdout
    text = str(value) + '\n'
    try:
        stream.write(text)
    except UnicodeEncodeError:
        encoding = getattr(stream, 'encoding', None) or 'ascii'
        stream.write(text.encode(encoding, errors='backslashreplace').decode(encoding))

def stamp(value):
    d = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if d.tzinfo is None:
        raise ValueError('检查时间必须包含时区')
    return d.astimezone(BEIJING)

def public_url(value, *, allow_sogou_token=False, _depth=0):
    if not isinstance(value, str) or not value or any(ord(c) < 33 for c in value) or '\\' in value:
        raise ValueError('无效URL')
    p = urlsplit(value)
    if p.scheme not in ('https', 'http') or not p.hostname or p.username or p.password:
        raise ValueError('仅接受无账号密码的HTTP(S)公开URL')
    if p.port not in (None, 80, 443):
        raise ValueError('不接受非标准端口')
    h = p.hostname.lower().rstrip('.')
    if h == 'localhost' or h.endswith(('.local', '.localhost', '.internal')) or '.' not in h:
        raise ValueError('拒绝本地地址')
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        ip = None
    if ip is not None and not ip.is_global:
        raise ValueError('拒绝私网地址')
    if re.search(r'antispider|wappoc_appmsgcaptcha|/mp/verify', value, re.I):
        raise ValueError('验证页面不是优惠入口')
    for k, v in parse_qsl(p.query, keep_blank_values=True):
        if SENSITIVE.match(k) and not (allow_sogou_token and h == 'weixin.sogou.com' and k.lower() == 'token'):
            raise ValueError('URL含个人或会话参数: ' + k)
        decoded = unquote(v)
        if decoded.startswith(('http://', 'https://')):
            if _depth >= 3:
                raise ValueError('嵌套URL过深')
            public_url(decoded, _depth=_depth + 1)
    return value
