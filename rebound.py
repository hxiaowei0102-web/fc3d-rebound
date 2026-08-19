# -*- coding: utf-8 -*-
"""
福彩3D 错后反弹软件 — 云端全自动生成脚本
==============================================
功能：多源抓取最新开奖 → 追加CSV(不覆盖) → 79条公式错2期信号检测 → 预测杀码 → 生成手机友好HTML
部署：GitHub Actions 定时运行（北京22:00/23:30/01:00 三重备份）+ GitHub Pages
仅用 Python 标准库，无第三方依赖。
"""
import csv, json, os, sys, ssl, re, time
from datetime import datetime, timezone, timedelta
from urllib.request import urlopen, Request

BJT = timezone(timedelta(hours=8))
CSV_PATH = 'data/fc3d-history.csv'
HTML_OUT = 'static/index.html'

# ============================================================
# 79 条固定公式（全史最长连错=2）
# 百23 / 十35 / 个21 = 79 条
# ============================================================
FORMULAS = {
    '百位': ["g+bsg+b^g+0", "1*g2+3*mn+5", "3*d1+3*g^b+2", "P+mx+g^b+2", "d1+bsg+sum4+8",
             "1*P+2*mx+5", "3*P+2*mn+5", "g+b2+bs+2", "2*d2+2*sum3+3", "b3+d3+S2+6",
             "3*g3+3*mx+0", "s2+mx+g^b+7", "s+d3+s^g+1", "1*S+1*md+3", "b+md+sum3+3",
             "s+md+sum4+3", "g+md+sum2+3", "2*s3+2*P+6", "b+d1+bs+5", "3*b+1*s3+4",
             "b3+mx+sum4+8", "g2+bs+sum2+1", "b2+d2+bg+6"],
    '十位': ["1*b+2*g2+9", "2*bsg+1*sum3+0", "s+d3+sum3+4", "2*b2+3*S+7", "g+S+P2+5",
             "P2+sum3+sum4+5", "g3+S+bs+6", "1*b2+1*sum3+5", "s+g+b2+5", "2*md+1*P2+5",
             "3*b2+1*b3+3", "1*md+2*bg+6", "P+bsg+P2+1", "2*g2+1*d3+9", "2*b3+1*bs+7",
             "s+mx+P2+4", "1*g2+3*d3+3", "s2+bs+bsg+3", "s3+S+d3+1", "1*g+1*g^b+9",
             "1*md+3*d2+4", "d1+d2+sum2+2", "b2+d2+sum2+5", "b3+P2+sum3+5", "1*b3+3*s3+4",
             "s2+g2+g^b+3", "2*b3+1*P+8", "s3+bs+bsg+8", "1*sg+3*sum3+2", "3*g2+1*bs+9",
             "b2+mn+sum2+9", "md+d1+d2+6", "3*mn+2*md+1", "b3+bg+sg+6", "P+md+b^g+1"],
    '个位': ["1*g+1*mn+2", "mn+d1+sg+1", "b2+s2+b3+0", "1*s2+2*s3+2", "g+mx+d2+4",
             "g+d3+b^g+5", "3*mx+3*S2+2", "b+S2+g^b+7", "d3+P2+sum2+6", "1*bg+1*sg+2",
             "1*s3+3*mx+7", "2*P+3*mx+6", "b2+sg+S2+8", "1*bg+3*sum2+2", "3*bg+2*sum3+0",
             "b3+P+md+2", "s3+d2+bsg+3", "1*s2+3*S2+2", "3*s+3*d2+8", "s2+g2+P2+4",
             "s2+P+bg+8"],
}
POS_IDX = {'百位': 0, '十位': 1, '个位': 2}
POS_COLOR = {'百位': '#e74c3c', '十位': '#f39c12', '个位': '#27ae60'}

# ============================================================
# 公式引擎：29 个原子特征 + 公式求值
# ============================================================
def _terms_of(b, s, g):
    mx = max(b, s, g); mn = min(b, s, g); md = b + s + g - mx - mn
    S = b + s + g; P = mx - mn
    return {
        'b': b, 's': s, 'g': g,
        'b2': (b * b) % 10, 's2': (s * s) % 10, 'g2': (g * g) % 10,
        'b3': (b * b * b) % 10, 's3': (s * s * s) % 10, 'g3': (g * g * g) % 10,
        'S': S, 'P': P, 'mx': mx, 'mn': mn, 'md': md,
        'd1': abs(b - s), 'd2': abs(b - g), 'd3': abs(s - g),
        'bs': (b * s) % 10, 'bg': (b * g) % 10, 'sg': (s * g) % 10, 'bsg': (b * s * g) % 10,
        'S2': (S * S) % 10, 'P2': (P * P) % 10,
        'sum2': (b + s) % 10, 'sum3': (s + g) % 10, 'sum4': (b + g) % 10,
        'b^g': (1 if g == 0 else b ** g) % 10,
        'g^b': (1 if b == 0 else g ** b) % 10,
        's^g': (1 if g == 0 else s ** g) % 10,
    }

def _eval_formula(b, s, g, formula):
    t = _terms_of(b, s, g)
    total = 0
    for part in formula.split('+'):
        part = part.strip()
        if '*' in part:
            c, feat = part.split('*')
            total += int(c) * t[feat]
        elif part.isdigit():
            total += int(part)
        else:
            total += t[part]
    return total % 10

# ============================================================
# 数据源（2026-08-19 老板确认）
#   ① 灰鸟主源：limit=1 只取最新一期，自带 next_code（跨年期号回绕 12-31→次年001）
#   ② 17500备份：官方级全量TXT(2002至今)，补漏 + 交叉验证（灰鸟 limit=1 会跳过中间期）
# ============================================================
HUINIAO_URL = 'https://api.huiniao.top/interface/home/lotteryHistory?type=fcsd&page=1&limit=1'
HUINIAO_URL20 = 'https://api.huiniao.top/interface/home/lotteryHistory?type=fcsd&page=1&limit=20'
TXT17500_URL = 'https://www.17500.cn/getData/3d.TXT'

# 换UA队列（17500 反爬 429 → 重试3次 + 换UA）
UA_LIST = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
    'Python-urllib/3.11',
]

def _fetch_url(url, retries=3, timeout=15):
    """带重试 + 换UA 的抓取（防 17500 反爬 429）"""
    last_err = None
    for i in range(retries):
        ua = UA_LIST[i % len(UA_LIST)]
        try:
            req = Request(url, headers={'User-Agent': ua})
            ctx = ssl._create_unverified_context()
            with urlopen(req, timeout=timeout, context=ctx) as resp:
                return resp.read().decode('utf-8', errors='ignore')
        except Exception as e:
            last_err = e
            if i < retries - 1:
                time.sleep(2)
    raise last_err

def _valid_issue(issue):
    """校验期号格式：20开头 + 7位数字（如 2026220）"""
    return isinstance(issue, str) and re.match(r'^20\d{5}$', issue) is not None

def _valid_digits(b, s, g):
    """校验百十个都是 0-9 的整数"""
    return all(isinstance(x, int) and 0 <= x <= 9 for x in [b, s, g])

def next_issue_of(issue):
    """本地兜底计算下一期期号，跨年回绕（福彩3D 年度最大约 356-358 期）"""
    year = int(issue[:4])
    seq = int(issue[4:])
    if seq >= 356:
        return f"{year + 1}001"
    return f"{year}{seq + 1:03d}"

def load_rows():
    rows = {}
    try:
        with open(CSV_PATH, 'r', encoding='utf-8') as f:
            for row in csv.DictReader(f):
                iss = row['issue']
                try:
                    rows[iss] = (int(row['hundreds']), int(row['tens']), int(row['ones']))
                except (KeyError, ValueError):
                    continue
    except FileNotFoundError:
        pass
    return rows

def fetch_and_merge():
    """灰鸟主源(limit=1) + 17500全量补漏 + 灰鸟limit=20次选补漏 + 交叉验证
    返回 (new_draws 按期号升序, next_code)
    """
    local_rows = load_rows()
    local_last = max(local_rows.keys(), key=int) if local_rows else None
    if local_last:
        print(f"  本地最新期号: {local_last}")

    merged = {}     # issue -> (b, s, g)
    next_code = None

    # ① 灰鸟主源 limit=1（拿最新一期 + next_code）
    try:
        raw = _fetch_url(HUINIAO_URL, retries=2)
        items = json.loads(raw)['data']['data']['list']
        for it in items:
            issue = it.get('code')
            if not _valid_issue(issue):
                continue
            try:
                b, s, g = int(it['one']), int(it['two']), int(it['three'])
            except (KeyError, ValueError, TypeError):
                continue
            if not _valid_digits(b, s, g):
                continue
            if local_last and int(issue) <= int(local_last):
                print(f"  [灰鸟] 期号{issue}<=本地, 跳过(旧数据)")
                continue
            merged[issue] = (b, s, g)
            nc = it.get('next_code')
            if _valid_issue(nc):
                next_code = nc
            print(f"  [灰鸟] 最新 {issue} = {b}{s}{g} next={next_code}")
    except Exception as e:
        print(f"  [灰鸟] 失败 {str(e)[:60]}")

    # ② 17500 全量补漏（重试3次+换UA，补所有漏掉的中间期）
    p17500_ok = False
    try:
        raw = _fetch_url(TXT17500_URL, retries=3)
        cnt = 0
        for l in raw.strip().split('\n'):
            parts = l.split()
            if len(parts) >= 5 and _valid_issue(parts[0]):
                issue = parts[0]
                if local_last and int(issue) <= int(local_last):
                    continue
                try:
                    b, s, g = int(parts[2]), int(parts[3]), int(parts[4])
                except ValueError:
                    continue
                if not _valid_digits(b, s, g):
                    continue
                if issue in merged and merged[issue] != (b, s, g):
                    print(f"  ⚠ 交叉验证不一致 {issue}: 灰鸟{merged[issue]} vs 17500{(b,s,g)}, 以灰鸟为准")
                    continue
                merged[issue] = (b, s, g)
                cnt += 1
        p17500_ok = True
        print(f"  [17500] 补漏{cnt}期")
    except Exception as e:
        print(f"  [17500] 失败 {str(e)[:60]}")

    # ③ 灰鸟 limit=20 次选补漏（17500 失败时，灰鸟自身补中间漏期）
    if not p17500_ok:
        try:
            raw = _fetch_url(HUINIAO_URL20, retries=2)
            items = json.loads(raw)['data']['data']['list']
            cnt = 0
            for it in items:
                issue = it.get('code')
                if not _valid_issue(issue):
                    continue
                if local_last and int(issue) <= int(local_last):
                    continue
                try:
                    b, s, g = int(it['one']), int(it['two']), int(it['three'])
                except (KeyError, ValueError, TypeError):
                    continue
                if not _valid_digits(b, s, g):
                    continue
                if issue in merged and merged[issue] != (b, s, g):
                    continue
                merged[issue] = (b, s, g)
                cnt += 1
            print(f"  [灰鸟limit=20] 补漏{cnt}期")
        except Exception as e:
            print(f"  [灰鸟limit=20] 失败 {str(e)[:60]}")

    new_draws = [(iss, merged[iss][0], merged[iss][1], merged[iss][2])
                 for iss in sorted(merged.keys(), key=int)]
    if new_draws:
        print(f"  合计新增 {len(new_draws)} 期: {new_draws[0][0]} ~ {new_draws[-1][0]}")
    else:
        print(f"  无新数据（数据源均无更新或全部失败）")
    return new_draws, next_code

def append_to_csv(draws):
    rows = load_rows()
    added = 0
    for item in draws:
        issue, b, s, g = item[0], item[1], item[2], item[3]
        if not (isinstance(issue, str) and issue.startswith('20') and 7 <= len(issue) <= 8):
            continue
        if not all(isinstance(x, int) and 0 <= x <= 9 for x in [b, s, g]):
            continue
        if issue in rows:
            continue
        rows[issue] = (b, s, g)
        added += 1
        print(f"  新增: {issue} = {b}{s}{g}")
    if added == 0:
        return 0
    with open(CSV_PATH, 'w', encoding='utf-8', newline='') as f:
        w = csv.writer(f)
        w.writerow(['issue', 'hundreds', 'tens', 'ones'])
        for iss in sorted(rows.keys()):
            b, s, g = rows[iss]
            w.writerow([iss, b, s, g])
    return added

# ============================================================
# 错后反弹算法
# ============================================================
def compute():
    rows_list = []
    issues = []
    with open(CSV_PATH, 'r', encoding='utf-8') as f:
        for r in csv.DictReader(f):
            issues.append(r['issue'])
            rows_list.append((int(r['hundreds']), int(r['tens']), int(r['ones'])))
    N = len(rows_list)
    if N < 3:
        return None  # 数据不足（至少需要3期才能计算错2期信号）

    # 逐期杀码命中序列 hit[pos][fi][t]
    hits = {}
    for pos in POS_IDX:
        idx = POS_IDX[pos]
        hits[pos] = []
        for f in FORMULAS[pos]:
            h = [None] * N
            for t in range(1, N):
                b, s, g = rows_list[t - 1]
                k = _eval_formula(b, s, g, f)
                h[t] = (k != rows_list[t][idx])
            hits[pos].append(h)

    # 当前预测（错2期公式）
    pred_issue = next_issue_of(issues[-1])
    lb, ls, lg = rows_list[-1]
    prediction = {}
    for pos in POS_IDX:
        idx = POS_IDX[pos]
        lst = []
        for fi, f in enumerate(FORMULAS[pos]):
            h = hits[pos][fi]
            if h[N - 2] is False and h[N - 1] is False:
                k = _eval_formula(lb, ls, lg, f)
                lst.append((f, k))
        prediction[pos] = lst

    # 全史反弹率 + 100期回测 + 明细（倒序，从近期开始）
    W = 100
    total_sig = total_hit = 0
    sig100 = hit100 = 0
    detail_rows = []
    for t in range(N - W, N):
        detail = {'issue': issues[t], 'actual': rows_list[t], 'pos': {}}
        for pos in POS_IDX:
            idx = POS_IDX[pos]
            cells = []
            for fi, f in enumerate(FORMULAS[pos]):
                h = hits[pos][fi]
                if h[t - 2] is False and h[t - 1] is False:
                    k = _eval_formula(rows_list[t - 1][0], rows_list[t - 1][1], rows_list[t - 1][2], f)
                    hit = h[t]
                    cells.append((f, k, hit))
                    # 统计回测命中
                    sig100 += 1
                    if hit:
                        hit100 += 1
            detail['pos'][pos] = cells
        detail_rows.append(detail)
    # 倒序（近期在前）
    detail_rows.reverse()

    # 全史反弹率
    for pos in POS_IDX:
        for fi, f in enumerate(FORMULAS[pos]):
            h = hits[pos][fi]
            for t in range(2, N):
                if h[t - 2] is False and h[t - 1] is False:
                    total_sig += 1
                    if h[t]:
                        total_hit += 1

    return {
        'pred_issue': pred_issue,
        'last_issue': issues[-1],
        'last_draw': f"{lb}{ls}{lg}",
        'prediction': prediction,
        'total_hit': total_hit, 'total_sig': total_sig,
        'hit100': hit100, 'sig100': sig100,
        'detail_rows': detail_rows,
        'n_formulas': sum(len(v) for v in FORMULAS.values()),
    }

# ============================================================
# HTML 生成（手机优先，自包含）
# ============================================================
def build_html(r):
    now = datetime.now(BJT).strftime('%Y-%m-%d %H:%M')
    stale_html = ('<div class="stale">⚠ 本次运行数据源暂无新数据，页面可能滞后于最新开奖，请稍后刷新。</div>'
                  if r.get('stale') else '')

    # 预测杀码卡片
    cards = []
    for pos in ['百位', '十位', '个位']:
        lst = r['prediction'][pos]
        color = POS_COLOR[pos]
        if lst:
            items = ''.join(
                f'<div class="fr"><span class="f">{f}</span><span class="arrow">→杀</span><span class="kill" style="background:{color}">{k}</span></div>'
                for f, k in lst)
        else:
            items = '<div class="empty">无错2期信号</div>'
        cards.append(
            f'<div class="card" style="border-top:3px solid {color}">'
            f'<div class="ch" style="background:{color}">{pos} · 错2期（{len(lst)}）</div>{items}</div>')

    # 100期明细（倒序）
    rows_html = []
    for d in r['detail_rows']:
        b, s, g = d['actual']
        tds = []
        for pos in ['百位', '十位', '个位']:
            cells = d['pos'][pos]
            act = {'百位': b, '十位': s, '个位': g}[pos]
            if cells:
                grp = ''.join(
                    f'<div class="fitem"><span class="f">{f}→杀{k}</span> <b class="{("ok" if hit else "no")}">{"✓" if hit else "✗"}</b></div>'
                    for f, k, hit in cells)
                tds.append(f'<td><div class="act">实际 {act}</div><div class="grp">{grp}</div></td>')
            else:
                tds.append(f'<td><div class="act dim">实际 {act}</div></td>')
        rows_html.append(
            f'<tr><td class="iss">{d["issue"]}</td>{tds[0]}{tds[1]}{tds[2]}</tr>')

    rate_all = r['total_hit'] / r['total_sig'] * 100 if r['total_sig'] else 0
    rate100 = r['hit100'] / r['sig100'] * 100 if r['sig100'] else 0

    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<title>福彩3D 错后反弹</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif; background:#f5f6fa; color:#2b3037; padding:14px; }}
.wrap {{ max-width:640px; margin:0 auto; }}
h1 {{ font-size:1.25rem; text-align:center; margin-bottom:4px; }}
.sub {{ text-align:center; color:#98a1ab; font-size:.74rem; margin-bottom:12px; }}
.kbar {{ background:#fff; border-radius:14px; box-shadow:0 1px 8px rgba(0,0,0,.06); padding:14px 16px; margin-bottom:12px; display:flex; gap:10px; justify-content:space-around; text-align:center; }}
.kbar .kpi .v {{ font-size:1.35rem; font-weight:bold; }}
.kbar .kpi .l {{ font-size:.68rem; color:#98a1ab; }}
.evidence {{ background:#fff; border-radius:14px; box-shadow:0 1px 8px rgba(0,0,0,.06); padding:12px 16px; margin-bottom:12px; font-size:.82rem; line-height:1.7; color:#3a4149; }}
.evidence b {{ color:#e74c3c; }}
.grid {{ display:grid; grid-template-columns:1fr; gap:12px; margin-bottom:12px; }}
@media(min-width:520px) {{ .grid {{ grid-template-columns:repeat(3,1fr); }} }}
.card {{ background:#fff; border-radius:14px; box-shadow:0 1px 8px rgba(0,0,0,.06); padding:12px 14px; }}
.ch {{ display:inline-block; color:#fff; font-size:.8rem; font-weight:bold; padding:3px 12px; border-radius:10px; margin-bottom:10px; }}
.fr {{ display:flex; align-items:center; gap:8px; padding:6px 8px; border-radius:8px; margin-bottom:5px; font-size:.8rem; background:#fdeaea; }}
.fr .f {{ font-family:Consolas,monospace; color:#2c3e50; flex:1; font-size:.72rem; }}
.fr .arrow {{ color:#98a1ab; font-size:.7rem; }}
.kill {{ display:inline-block; min-width:34px; height:34px; line-height:34px; text-align:center; border-radius:10px; color:#fff; font-weight:bold; font-size:1.15rem; }}
.empty {{ color:#b6bdc5; font-size:.78rem; padding:6px 8px; }}
.panel {{ background:#fff; border-radius:14px; box-shadow:0 1px 8px rgba(0,0,0,.06); padding:14px 16px; margin-bottom:12px; }}
.panel h2 {{ font-size:.9rem; color:#3a4149; margin-bottom:10px; }}
.sum {{ display:flex; gap:10px; margin-bottom:10px; }}
.sum > div {{ flex:1; background:#fdeaea; border-radius:12px; padding:12px; text-align:center; }}
.sum .v {{ font-size:1.5rem; font-weight:bold; color:#e74c3c; }}
.sum .l {{ font-size:.7rem; color:#98a1ab; }}
.tblwrap {{ max-height:520px; overflow:auto; -webkit-overflow-scrolling:touch; }}
table {{ width:100%; border-collapse:collapse; min-width:480px; }}
th,td {{ padding:6px 7px; font-size:.72rem; text-align:left; border-bottom:1px solid #f0f2f5; vertical-align:top; }}
th {{ background:#fafbfc; color:#8a929c; font-weight:600; text-align:center; position:sticky; top:0; }}
.iss {{ text-align:center; font-weight:bold; color:#5a626d; white-space:nowrap; }}
.act {{ font-weight:bold; color:#3a4149; font-size:.74rem; margin-bottom:2px; }}
.act.dim {{ color:#b6bdc5; font-weight:normal; }}
.grp {{ margin-bottom:2px; }}
.fitem {{ font-size:.7rem; line-height:1.5; }}
.f {{ font-family:Consolas,monospace; color:#2c3e50; }}
.ok {{ color:#27ae60; }}
.no {{ color:#e74c3c; }}
.note {{ background:#fff; border-radius:14px; box-shadow:0 1px 8px rgba(0,0,0,.06); padding:14px 16px; margin-top:4px; font-size:.8rem; line-height:1.8; color:#3a4149; }}
.note b {{ color:#e74c3c; }}
.stale {{ background:#fff7e6; border:1px solid #ffd591; color:#ad6800; border-radius:12px; padding:10px 14px; margin-bottom:12px; font-size:.78rem; line-height:1.6; }}
.foot {{ text-align:center; color:#b6bdc5; font-size:.68rem; margin-top:12px; }}
</style>
</head>
<body>
<div class="wrap">
<h1>福彩3D 错后反弹</h1>
<div class="sub">{r['n_formulas']} 条固定公式（全史连错=2）· 预测杀码 · 100期真实回测 · 生成 {now}</div>

<div class="kbar">
<div class="kpi"><div class="v" style="color:#e74c3c">{r['pred_issue']}</div><div class="l">预测期号</div></div>
<div class="kpi"><div class="v">{r['last_issue']}</div><div class="l">最新期号</div></div>
<div class="kpi"><div class="v">{r['last_draw']}</div><div class="l">最新开奖</div></div>
</div>

<div class="evidence"><b>错2期信号：</b>连错两期后必反弹。全史反弹率 <b>{rate_all:.1f}%</b>（{r['total_hit']}/{r['total_sig']}），是最强信号。</div>

{stale_html}

<div class="grid">{''.join(cards)}</div>

<div class="panel">
<h2>最近 100 期真实回测（倒序 · 三位置并排）</h2>
<div class="sum"><div><div class="v">{rate100:.1f}%</div><div class="l">错2期信号命中（{r['hit100']}/{r['sig100']}）</div></div></div>
<div class="tblwrap"><table><thead><tr><th>期号</th><th style="color:#e74c3c">百位</th><th style="color:#f39c12">十位</th><th style="color:#27ae60">个位</th></tr></thead><tbody>{''.join(rows_html)}</tbody></table></div>
</div>

<div class="note"><b>用法说明：</b>① 🔴错2期 = 某公式连续两期杀错，下一期反弹（全史命中率 {rate_all:.1f}%）；② 彩色方块 = 该公式预测下一期「不会出」的杀码；③ 明细红色公式 = 错2期信号、绿✓=命中、红✗=杀错；④ 统计倾向 ≠ 保证，理性参考。</div>
<div class="foot">数据源：fc3d-history.csv（{r['last_issue']} 期）· 云端全自动更新 · 更新于 {now}</div>
</div>
</body>
</html>'''
    return html

# ============================================================
# 主流程
# ============================================================
def main():
    print(f"=== 福彩3D 错后反弹 自动更新 ===")
    print(f"  时间(北京): {datetime.now(BJT).strftime('%Y-%m-%d %H:%M:%S')}")

    # 1. 抓取（灰鸟主源 limit=1 + 17500 全量补漏 + 交叉验证）
    print(f"\n[1/3] 抓取(灰鸟主源+17500补漏)...")
    new_draws, next_code = fetch_and_merge()

    # 2. 追加CSV
    print(f"\n[2/3] 更新CSV...")
    if new_draws:
        added = append_to_csv(new_draws)
        print(f"  新增{added}期")
    else:
        print(f"  无新数据，跳过CSV更新")

    # 3. 生成HTML
    print(f"\n[3/3] 生成HTML...")
    r = compute()
    if r is None:
        print("  数据不足（<3期），跳过生成")
        sys.exit(0)
    if next_code and _valid_issue(next_code):
        r['pred_issue'] = next_code
    r['stale'] = (len(new_draws) == 0)  # 本次无新数据 → 页面标注可能滞后
    html = build_html(r)
    os.makedirs(os.path.dirname(HTML_OUT), exist_ok=True)
    with open(HTML_OUT, 'w', encoding='utf-8') as f:
        f.write(html)

    print(f"  预测期号: {r['pred_issue']}")
    print(f"  最新开奖: {r['last_draw']}")
    print(f"  错2期: 百{len(r['prediction']['百位'])} 十{len(r['prediction']['十位'])} 个{len(r['prediction']['个位'])}")
    print(f"  全史反弹: {r['total_hit']}/{r['total_sig']}")
    print(f"  100期反弹: {r['hit100']}/{r['sig100']}")
    print(f"  ✓ 完成")

if __name__ == '__main__':
    main()
