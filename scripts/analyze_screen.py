#!/usr/bin/env python3
"""质量筛选结果分析：验证排除名单 + 看通过名单top。"""
import json, sqlite3, sys, importlib.util
sys.path.insert(0, 'scripts')
spec = importlib.util.spec_from_file_location('qs', 'scripts/quality_screen.py')
qs = importlib.util.module_from_spec(spec); spec.loader.exec_module(qs)

conn = sqlite3.connect('research.db')
meta = {r[0]: (r[1], r[2]) for r in conn.execute('SELECT ticker, company_name, market_cap_b FROM companies')}
cache = json.load(open('data/quality_metrics.json'))

print("=== 排除的 60 家（前 25，验证筛选在抓坏公司）===")
excl = []
for t, m in cache.items():
    if not m.get('_ok'): continue
    v, d = qs.screen_one(t, m)
    if v == '排除':
        fails = [k for k, dd in d.items() if k != '③利息' and dd[0] is False]
        excl.append((t, meta.get(t, ('?', 0))[0][:26], fails))
excl.sort(key=lambda x: -len(x[2]))
for t, n, f in excl[:25]:
    print(f"  {t:<7} {n:<26} 不过:{f}")
print(f"  ... 共 {len(excl)} 家")

print()
print("=== 通过名单中 ROE 最高 15（质量龙头）===")
passed = []
for t, m in cache.items():
    if not m.get('_ok'): continue
    v, d = qs.screen_one(t, m)
    if v == '通过':
        roe = d.get('①ROE', (None, None))[1]
        nm = d.get('⑥净利率', (None, None))[1]
        gm = d.get('④毛利率', (None, None))[1]
        passed.append((t, meta.get(t, ('?', 0))[0][:26], roe, gm, nm, meta.get(t, (0, 0))[1]))
passed.sort(key=lambda x: -(x[2] or 0))
for t, n, roe, gm, nm, mcap in passed[:15]:
    print(f"  {t:<7} {n:<26} ROE={roe} 毛利={gm} 净利={nm} 市值={mcap and round(mcap)}B")

print()
print(f"通过共 {len(passed)} 家 | 市值≥300亿的通过家数: {sum(1 for p in passed if p[5] and p[5]>=300)}")
