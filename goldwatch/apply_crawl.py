#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SSY 作战室 · 采集结果回写页面
读取 crawl-latest.json，更新 index.html 的 ITEMS（low/med/chg/an/tr/yld/profit）与快照时间戳，
随后由调用方（或本脚本 --push）负责 git 提交推送。
退出码：0 成功 / 2 cookie 过期（不改动页面）/ 1 其他错误
"""
import json, re, sys, os, subprocess, datetime

GW = '/mnt/agents/output/goldwatch'
REPO = '/mnt/agents/output/.work-share'
PAGE = os.path.join(REPO, 'auction-ledger/index.html')

def main(do_push=False):
    cr = json.load(open(os.path.join(GW, 'crawl-latest.json'), encoding='utf-8'))
    if cr.get('error') == 'cookie_expired':
        print('cookie 已过期，跳过回写（需重新扫码登录）')
        sys.exit(2)
    ts = cr['ts']
    date_cn = ts[:10]
    src = open(PAGE, encoding='utf-8').read()

    m = re.search(r'const ITEMS = (\[[\s\S]*?\]);\s*const GOLD', src)
    items = json.loads(m.group(1))
    gm = re.search(r'const GOLD = (\{[^}]+\});', src)
    gold = json.loads(gm.group(1))
    rate = gold['rate']

    # ---- 金价块：最新价 + 49小时序列 + 五区报价 + 买金线 ----
    g = cr.get('gold')
    if g and g.get('rate'):
        rate = g['rate']                                # 元/万金
        last = g.get('prev24') or gold.get('last')
        chg = round((rate / last - 1) * 100, 1) if last else gold.get('chg')
        line = round(10000 / (rate * 0.95))             # 买金线 = 税后盈亏平衡 金/元
        asof = g['asof'][:16].replace('T', ' ')         # 2026-08-16T18:00:00+08:00 -> 2026-08-16 18:00
        new_gold = {'rate': rate, 'last': last, 'chg': chg, 'line': line, 'asof': asof}
        src = src[:gm.start(1)] + json.dumps(new_gold, ensure_ascii=False) + src[gm.end(1):]
        hourly = [pt['rate'] for pt in g.get('hourly', []) if pt.get('rate')]
        if len(hourly) >= 10:
            src = re.sub(r'const GOLD_HOURLY = \[[^\]]*\];',
                         'const GOLD_HOURLY = [' + ', '.join(str(v) for v in hourly) + '];', src)
        order = ['蓝蜗牛', '小白兔', '漂漂猪', '蘑菇仔', '绿水灵']
        sv = []
        for nm in order:
            s = g['servers'].get(nm)
            if not s:
                continue
            p = g.get('servers_prev', {}).get(nm)
            schg = round((s['rate'] / p - 1) * 100, 1) if p else 0
            sv.append([nm, s['rate'], schg, s.get('stock', 1)])
        if sv:
            src = re.sub(r'const SERVERS = \[[\s\S]*?\];',
                         'const SERVERS = ' + json.dumps(sv, ensure_ascii=False) + ';', src)
        # 金价标题行（静态 HTML）
        src = re.sub(r'最新 \d{2}-\d{2} \d{2}:\d{2} · [\d.]+ 万金/元',
                     f'最新 {asof[5:16]} · {g["g"]:.4f} 万金/元', src)
        # 买金线数值（正文/沙盘/术语表三处）
        src = re.sub(r'买金线 [\d,]+', f'买金线 {line:,}', src)
        src = re.sub(r'（当前 [\d,]+）', f'（当前 {line:,}）', src)
        print(f'金价回写: {asof} 漂漂猪 {g["g"]:.4f} 万金/元 · 买金线 {line:,} · 24h {chg:+.1f}%')
    changed = 0
    for it in items:
        d = cr['items'].get(it['id'])
        if not d or d.get('missing'):
            continue
        if d.get('low'):  it['low'] = d['low']
        if d.get('med'):  it['med'] = d['med']
        if d.get('chg') is not None: it['chg'] = d['chg']
        if d.get('n_day') is not None: it['an'] = d['n_day']
        if d.get('tr7'):  it['tr'] = d['tr7']
        it['yld'] = round(it['med'] * 0.95 / it['cost'])
        it['profit'] = round(it['med'] * 0.95 / 10000 * rate - it['cost'], 2)
        changed += 1
    src = src[:m.start(1)] + json.dumps(items, ensure_ascii=False) + src[m.end(1):]

    # 快照时间戳
    src = re.sub(r'全品种 \d{4}-\d{2}-\d{2}( \d{2}:\d{2})? 实盘复核', f'全品种 {date_cn} {ts[11:16]} 实盘复核', src)
    src = re.sub(r'(数据复核[ ·]*)\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}',
                 lambda mm: mm.group(1) + ts.replace('-', '/') + ':00', src)
    open(PAGE, 'w', encoding='utf-8').write(src)
    print(f'回写 {changed}/{len(items)} 件，快照 {ts}')

    if do_push:
        pat = open(os.path.join(GW, '.github-pat')).read().strip()
        subprocess.run(['git', 'add', '-A'], cwd=REPO)
        subprocess.run(['git', 'commit', '-m', f'每小时全品种采集回写 {ts}'], cwd=REPO, capture_output=True)
        for i in range(3):
            r = subprocess.run(['git', '-c', 'http.version=HTTP/1.1', 'push',
                                f'https://x-access-token:{pat}@github.com/shiwo233/share.git', 'HEAD:main'],
                               cwd=REPO, capture_output=True, text=True)
            if r.returncode == 0:
                print('已推送')
                return
            print(f'推送重试 {i+1}')
        print('推送失败（下轮再试）')

if __name__ == '__main__':
    main('--push' in sys.argv)
