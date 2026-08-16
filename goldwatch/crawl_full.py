#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SSY 作战室 · mxdc 全品种采集器（漂漂猪 server=4）
链路：列表页领 st → 检索 q=itemid → 结果卡(ticket+最低在售+较上次) → item.php → AES-256-GCM 解密 trend
输出：/mnt/agents/output/goldwatch/crawl-latest.json
cookie 失效（401）时写 crawl-latest.json {"error":"cookie_expired"} 并退出码 2
"""
import json, re, os, sys, time, random, base64, datetime
import requests
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

BASE = 'https://mxdc.dvg.cn'
GW = '/mnt/agents/output/goldwatch'
PAGE = '/mnt/agents/output/.work-share/auction-ledger/index.html'
OUT = os.path.join(GW, 'crawl-latest.json')
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36'}
GAP = (22, 27)          # 每件间隔秒数（限流红线 22s）

def log(*a):
    print(datetime.datetime.now().strftime('%H:%M:%S'), *a, flush=True)

def fresh_st(sess):
    r = sess.get(BASE + '/tools/market/?server=4', timeout=30)
    if r.status_code == 401:
        raise RuntimeError('cookie_expired')
    m = re.search(r'name="st" value="([^"]+)"', r.text)
    return m.group(1)

def decrypt_item(html):
    m = re.search(r'<script id="auction-market-payload"[^>]*data-key="([^"]+)"[^>]*data-iv="([^"]+)"[^>]*data-tag="([^"]+)"[^>]*>([^<]+)</script>', html)
    if not m:
        return None
    key, iv, tag, ct = m.groups()
    raw = base64.b64decode(ct) + base64.b64decode(tag)
    return json.loads(AESGCM(base64.b64decode(key)).decrypt(base64.b64decode(iv), raw, None))

def crawl_one(sess, iid):
    st = fresh_st(sess)
    r = sess.get(f'{BASE}/tools/market/?st={st}&server=4&q={iid}', timeout=30)
    if r.status_code == 401:
        raise RuntimeError('cookie_expired')
    cm = re.search(r'href="(/tools/market/item\.php\?id=' + iid + r'&amp;server=4&amp;ticket=([^"]+))"', r.text)
    if not cm:
        return {'id': iid, 'missing': True}
    # 结果卡：最低在售 + 较上次
    card = re.search(r'market-result-card[\s\S]*?最低价</small>\s*<strong>([\d,]+)', r.text)
    low = int(card.group(1).replace(',', '')) if card else None
    item = sess.get(BASE + cm.group(1).replace('&amp;', '&'), timeout=30)
    data = decrypt_item(item.text)
    if not data:
        return {'id': iid, 'missing': True}
    tr = [t for t in data['trend'] if t.get('n') and t.get('s') == 4]  # 只取漂漂猪
    cs = [t['c'] for t in tr]
    med = sorted(cs)[len(cs) // 2] if cs else None
    days = sorted({t['d'] for t in tr}, reverse=True)[:7]
    n_day = round(sum(t['n'] for t in tr if t['d'] in days) / max(len(days), 1), 1)
    # 涨跌幅：最新一个有量的 c vs 前一个
    chg = None
    if len(cs) >= 2 and cs[-2]:
        chg = round((cs[-1] / cs[-2] - 1) * 100, 1)
    tr7 = cs[-6:] + ([low] if low else [])   # 近6个成交价 + 当前最低挂价
    return {'id': iid, 'low': low, 'med': med, 'chg': chg, 'n_day': n_day,
            'tr7': tr7, 'rate': data.get('rates', {}).get('4')}

def fetch_gold():
    """price-trend 公开页：最新五区报价 + 近49小时漂漂猪序列（无需 cookie）"""
    r = requests.get(BASE + '/tools/price-trend/', headers=UA, timeout=30)
    m = re.search(r'window\.PRICE_TREND_DATA = (\{.*?\});\s*</script>', r.text, re.S)
    d = json.loads(m.group(1))
    def q_of(items, name):
        for it in items:
            if it['area_name'] == name:
                return it['quote']
        return None
    latest = d['latest']
    hh = d['history_hour']
    hourly = []
    for e in hh:
        q = q_of(e['items'], '漂漂猪')
        if q:
            hourly.append({'t': e['collected_at'], 'rate': q['wan_gold_to_yuan'], 'g': q['yuan_to_wan_gold']})
    prev_items = hh[-25]['items'] if len(hh) >= 25 else hh[0]['items']
    prev_map = {it['area_name']: it['quote']['wan_gold_to_yuan'] for it in prev_items}
    servers = {}
    for it in latest['items']:
        servers[it['area_name']] = {'g': it['quote']['yuan_to_wan_gold'],
                                    'rate': it['quote']['wan_gold_to_yuan'],
                                    'stock': it['quote'].get('stock', 1)}
    q0 = q_of(latest['items'], '漂漂猪')
    asof = latest['collected_at']
    if asof.endswith('Z'):  # UTC -> +08:00
        dt = datetime.datetime.fromisoformat(asof.replace('Z', '+00:00')) + datetime.timedelta(hours=8)
        asof = dt.strftime('%Y-%m-%dT%H:%M:%S+08:00')
    return {'asof': asof, 'rate': q0['wan_gold_to_yuan'], 'g': q0['yuan_to_wan_gold'],
            'prev24': prev_map.get('漂漂猪'), 'hourly': hourly[-49:],
            'servers': servers, 'servers_prev': prev_map}

def main():
    cookie = open(os.path.join(GW, '.cookie')).read().strip()
    src = open(PAGE, encoding='utf-8').read()
    items = json.loads(re.search(r'const ITEMS = (\[[\s\S]*?\]);\s*const GOLD', src).group(1))
    ids = [(it['id'], it['n']) for it in items]
    log(f'共 {len(ids)} 件待采')
    sess = requests.Session()
    sess.headers.update({**UA, 'Cookie': cookie})
    out = {'ts': datetime.datetime.now().strftime('%Y-%m-%d %H:%M'), 'items': {}}
    try:
        out['gold'] = fetch_gold()
        json.dump(out, open(OUT, 'w'), ensure_ascii=False, indent=1)  # 金价先落盘
        log('金价 OK:', out['gold']['asof'], '漂漂猪', out['gold']['g'], '万金/元')
    except Exception as e:
        log('金价采集失败（不影响道具采集）:', repr(e))
    try:
        for k, (iid, name) in enumerate(ids):
            try:
                d = crawl_one(sess, iid)
            except RuntimeError as e:
                if 'cookie_expired' in str(e):
                    json.dump({'error': 'cookie_expired', 'ts': out['ts']}, open(OUT, 'w'), ensure_ascii=False)
                    log('COOKIE 过期，已写状态文件')
                    sys.exit(2)
                raise
            out['items'][iid] = d
            log(f'[{k+1}/{len(ids)}] {name} ->', {x: d.get(x) for x in ('low', 'med', 'chg', 'n_day')})
            json.dump(out, open(OUT, 'w'), ensure_ascii=False, indent=1)  # 增量落盘：中途超时也保住已采数据
            if k < len(ids) - 1:
                time.sleep(random.uniform(*GAP))
    except Exception as e:
        out['partial_error'] = repr(e)
        json.dump(out, open(OUT, 'w'), ensure_ascii=False, indent=1)
        log('异常中断:', e)
        sys.exit(1)
    json.dump(out, open(OUT, 'w'), ensure_ascii=False, indent=1)
    log('完成', len(out['items']), '件 ->', OUT)

if __name__ == '__main__':
    main()
