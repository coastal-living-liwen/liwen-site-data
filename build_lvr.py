"""內政部實價登錄開放資料 → 網站用的 JSON（沙鹿／梧棲／清水／龍井／西屯）

資料來源：內政部不動產成交案件實際資訊資料供應系統（政府資料開放授權條款）
  季資料  https://plvr.land.moi.gov.tw/DownloadSeason?season=115S3&fileName=B_lvr_land_A.csv
  本期    https://plvr.land.moi.gov.tw/Download?fileName=B_lvr_land_A.csv   （每月 1、11、21 日發布）
  歷史批 https://plvr.land.moi.gov.tw/DownloadHistory?type=history&fileName=20261001  （全國 zip）
  A = 成屋（不動產買賣）、B = 預售屋

為什麼要抓「歷史批」：本期只留最新一批，季檔要到季末才出。中間那幾批（例：10/1 那批要等
明年 1 月的 115S4 季檔）只放在歷史批，不抓就會漏（實測 10/13 漏了梧棲 3 筆 8 月的成交）。
抓過的批次記在 lvr/releases_done.json，下次跳過；資料已併進年度檔，不會消失。

產出（--out 底下的 lvr/）：
  meta.json          同步時間、本期資訊、各區近 6 個月摘要、可選年份
  A_recent.json      近 6 個月成屋        B_recent.json   近 6 個月預售
  A_115.json …       依「交易年份」分檔（也是合併用的資料庫，依「編號」去重）

用法：
  python build_lvr.py --out ../website-v2/data                  # 平常：最近 2 季 + 本期
  python build_lvr.py --out ../website-v2/data --from 104S1     # 第一次：回補歷史
"""
import argparse
import csv
import io
import json
import re
import ssl
import statistics
import sys
import time
import urllib.request
import zipfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

DISTRICTS = ["沙鹿區", "梧棲區", "清水區", "龍井區", "西屯區"]
MIN_YEAR = 104
SEASON_URL = "https://plvr.land.moi.gov.tw/DownloadSeason?season={season}&fileName=B_lvr_land_{kind}.csv"
CURRENT_URL = "https://plvr.land.moi.gov.tw/Download?fileName=B_lvr_land_{kind}.csv"
HISTORY_URL = "https://plvr.land.moi.gov.tw/DownloadHistory?type=history&fileName={date}"
TW = timezone(timedelta(hours=8))
PING = 3.30579
csv.field_size_limit(10_000_000)  # 110S4 B 有超長欄位，預設 128KB 會報錯
# 仍驗證憑證鏈；只關掉 Python 3.13 的嚴格格式檢查（政府網站憑證缺 Subject Key Identifier 會被擋）
SSL_CTX = ssl.create_default_context()
SSL_CTX.verify_flags &= ~ssl.VERIFY_X509_STRICT

CN = {"零": 0, "一": 1, "二": 2, "兩": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def fetch(url, cache=None):
    if cache and cache.exists():
        return cache.read_bytes()
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (liwen-site-data)"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=120, context=SSL_CTX) as r:
                body = r.read()
                ctype = r.headers.get("Content-Type", "")
            break
        except Exception:
            if attempt == 2:
                raise
            time.sleep(5)
    if "html" in ctype or len(body) < 500:
        return None  # 該季尚未發布
    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(body)
    return body


def cn_num(s):
    """七 → 7、十五 → 15、二十一 → 21"""
    if not s:
        return None
    if s.isdigit():
        return int(s)
    if "十" in s:
        a, _, b = s.partition("十")
        return (CN.get(a, 1) if a else 1) * 10 + (CN.get(b, 0) if b else 0)
    return CN.get(s)


def floor_text(level, total):
    t = cn_num(re.sub(r"[層樓]", "", total or "")) if total else None
    lv = (level or "").strip()
    if not lv:
        return f"/{t}" if t else ""
    if lv.startswith("全"):
        lv_s = "全"
    else:
        parts = []
        for p in re.split(r"[，,]", lv):
            p = p.strip().replace("層", "")
            if p.startswith("地下"):
                n = cn_num(p[2:])
                parts.append(f"B{n}" if n else p)
            else:
                n = cn_num(p)
                parts.append(str(n) if n is not None else p)
        lv_s = parts[0] if len(parts) == 1 else f"{parts[0]}~{parts[-1]}" if len(parts) > 3 else "、".join(parts)
    return f"{lv_s}/{t}" if t else lv_s


FULLWIDTH = {c: c - 0xFEE0 for c in range(0xFF01, 0xFF5F)}
FULLWIDTH[0x3000] = 0x20


def half(s):
    """臺中市梧棲區大同街２８８號 → 臺中市梧棲區大同街288號"""
    return (s or "").translate(FULLWIDTH).strip()


def addr_key(addr):
    """門牌 → 對照用的 key，只取到「號」（同一棟樓不分樓層）
    臺中市梧棲區八德東路６５號九樓之８ → 梧棲區|八德東路65號；地號、沒有「號」的回 None"""
    s = re.sub(r"^(臺中市|台中市)", "", half(addr))
    m = re.match(r"^(\S{1,3}?區)(.+?號)", s)
    if not m or "地號" in m.group(2):
        return None
    return m.group(1) + "|" + m.group(2)


def tx_key(addr, dt):
    """單筆交易 key：完整門牌（不含縣市）＋交易日，例 梧棲區大仁路二段311巷46號十一樓之3|1150605"""
    return re.sub(r"^(臺中市|台中市)", "", half(addr)) + "|" + str(dt)


def load_communities():
    """community_names.json：官網匯出的「社區簡稱」對照表（import_community.py 產生）
    map = 整棟（門牌到「號」）；tx = 逐筆例外（官網那一筆跟整棟不同，多半是官網空白）"""
    p = Path(__file__).parent / "community_names.json"
    if not p.exists():
        return {}, {}
    d = json.loads(p.read_text(encoding="utf-8"))
    return d.get("map", {}), d.get("tx", {})


def f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def roc_int(s):
    s = (s or "").strip()
    if not s.isdigit() or len(s) < 6:
        return None
    return int(s)


def ym(d):
    """1150815 → (115, 8)"""
    return d // 10000, (d // 100) % 100


def category(target, btype):
    if target.startswith("土地"):
        return "l", "土地"
    if target == "車位":
        return None, None
    b = btype or ""
    for key, k, label in (("住宅大樓", "b", "大樓"), ("華廈", "b", "華廈"), ("公寓", "b", "公寓"), ("套房", "b", "套房"),
                          ("透天", "h", "透天"), ("店面", "s", "店面"), ("辦公", "s", "辦公"), ("廠辦", "s", "廠辦"),
                          ("工廠", "s", "工廠")):
        if key in b:
            return k, label
    return "o", "其他"


def parse(body, kind):
    text = body.decode("utf-8-sig", errors="replace")
    rows = []
    for r in csv.DictReader(io.StringIO(text)):
        dist = (r.get("鄉鎮市區") or "").strip()
        if dist not in DISTRICTS:
            continue
        dt = roc_int(r.get("交易年月日"))
        if not dt:
            continue
        k, label = category((r.get("交易標的") or "").strip(), r.get("建物型態"))
        if not k:
            continue
        total = f(r.get("總價元"))
        if total <= 0:
            continue
        bldg = f(r.get("建物移轉總面積平方公尺"))
        park_m2 = f(r.get("車位移轉總面積平方公尺"))
        park_p = f(r.get("車位總價元"))
        if k == "l":
            area = f(r.get("土地移轉總面積平方公尺")) / PING
            unit = total / area / 10000 if area > 0 else None
        else:
            area = (bldg - park_m2 if park_p > 0 else bldg) / PING
            u_m2 = f(r.get("單價元平方公尺"))
            unit = u_m2 * PING / 10000 if u_m2 > 0 else (total - park_p) / area / 10000 if area > 0 else None
        row = {
            "id": (r.get("編號") or "").strip(),
            "d": dist[:-1],
            "dt": dt,
            "a": half(r.get("土地位置建物門牌")),
            "k": k,
            "ty": label,
            "p": round(total / 10000, 1),
            "u": round(unit, 2) if unit else None,
            "s": round(area, 2) if area > 0 else None,
        }
        built = roc_int(r.get("建築完成年月"))
        if built and k != "l":
            by, bm = ym(built)
            ty_, tm = ym(dt)
            age = (ty_ + tm / 12) - (by + bm / 12)
            if 0 <= age < 100:
                row["ag"] = round(age, 1)
        rm, lr, bt = r.get("建物現況格局-房"), r.get("建物現況格局-廳"), r.get("建物現況格局-衛")
        if k in ("b", "h") and any(x not in (None, "", "0") for x in (rm, lr, bt)):
            row["r"] = f"{rm or 0}房{lr or 0}廳{bt or 0}衛"
        fl = floor_text(r.get("移轉層次"), r.get("總樓層數"))
        if fl and k != "l":
            row["fl"] = fl
        ptype = (r.get("車位類別") or "").strip()
        if ptype:
            row["pk"] = ptype + (f" {round(park_p / 10000)}萬" if park_p > 0 else "")
        note = (r.get("備註") or "").strip()
        if note:
            row["n"] = note[:80]
        if kind == "B":
            name = half(r.get("建案名稱"))
            if name:
                row["pj"] = name
            unit_no = half(r.get("棟及號"))
            if unit_no:
                row["bn"] = unit_no
            if (r.get("解約情形") or "").strip():
                row["x"] = 1
        if row["id"]:
            rows.append(row)
    return rows


def season_list(start):
    now = datetime.now(TW)
    cy, cq = now.year - 1911, (now.month - 1) // 3 + 1
    y, q = int(start[:3]), int(start[-1])
    out = []
    while (y, q) <= (cy, cq):
        out.append(f"{y}S{q}")
        y, q = (y, q + 1) if q < 4 else (y + 1, 1)
    return out


def latest_seasons(n):
    """從本季往回找，回傳最近 n 個已發布的季別"""
    now = datetime.now(TW)
    y, q = now.year - 1911, (now.month - 1) // 3 + 1
    found = []
    for _ in range(8):
        s = f"{y}S{q}"
        if fetch(SEASON_URL.format(season=s, kind="A")):
            found.append(s)
            if len(found) == n:
                break
        y, q = (y, q - 1) if q > 1 else (y - 1, 4)
    return found


def recent_release_dates(now, months=4):
    """近幾個月所有可能的發布日（每月 1、11、21），不含未來"""
    out = []
    y, m = now.year, now.month
    for _ in range(months + 1):
        for d in (21, 11, 1):
            dt = datetime(y, m, d, tzinfo=TW)
            if dt.date() <= now.date():
                out.append(dt.strftime("%Y%m%d"))
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    return sorted(out)


def fetch_history(date):
    """歷史批 zip → {"A": csv bytes, "B": csv bytes}；該批已併進季檔或不存在 → None"""
    body = fetch(HISTORY_URL.format(date=date))
    if not body:
        return None
    z = zipfile.ZipFile(io.BytesIO(body))
    names = {n.lower(): n for n in z.namelist()}
    return {k: z.read(names[f"b_lvr_land_{k.lower()}.csv"]) for k in ("A", "B") if f"b_lvr_land_{k.lower()}.csv" in names}


def release_date(now):
    """內政部每月 1、11、21 日發布，回傳最近一次（民國字串）"""
    day = 21 if now.day >= 21 else 11 if now.day >= 11 else 1
    return f"{now.year - 1911}/{now.month:02d}/{day:02d}"


def load_year(path):
    if path.exists():
        return {r["id"]: r for r in json.loads(path.read_text(encoding="utf-8"))}
    return {}


def dump(path, rows):
    path.write_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def median(vals):
    vals = [v for v in vals if v]
    return round(statistics.median(vals), 1) if vals else None


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--from", dest="start", help="回補起始季別，例如 104S1")
    ap.add_argument("--cache", help="季資料下載快取資料夾（回補時建議指定）")
    args = ap.parse_args()

    out = Path(args.out) / "lvr"
    out.mkdir(parents=True, exist_ok=True)
    cache = Path(args.cache) if args.cache else None
    now = datetime.now(TW)

    seasons = season_list(args.start) if args.start else latest_seasons(2)
    print("季別：", ", ".join(seasons))

    incoming = {"A": [], "B": []}
    for s in seasons:
        for kind in ("A", "B"):
            body = fetch(SEASON_URL.format(season=s, kind=kind), cache / f"{s}_{kind}.csv" if cache else None)
            if body:
                rows = parse(body, kind)
                incoming[kind] += rows
                print(f"  {s} {kind}: {len(rows)} 筆")
            else:
                print(f"  {s} {kind}: 尚未發布")

    current_ids = {"A": set(), "B": set()}
    for kind in ("A", "B"):
        body = fetch(CURRENT_URL.format(kind=kind))
        rows = parse(body, kind) if body else []
        current_ids[kind] = {r["id"] for r in rows}
        incoming[kind] += rows
        print(f"  本期 {kind}: {len(rows)} 筆")

    # 季檔和本期之間的歷史批（抓過的跳過）
    done_path = out / "releases_done.json"
    done = set(json.loads(done_path.read_text(encoding="utf-8"))) if done_path.exists() else set()
    for date in recent_release_dates(now):
        if date in done:
            continue
        files = fetch_history(date)
        if not files:
            continue
        for kind, body in files.items():
            rows = parse(body, kind)
            incoming[kind] += rows
            print(f"  歷史批 {date} {kind}: {len(rows)} 筆")
        done.add(date)
    done_path.write_text(json.dumps(sorted(done)), encoding="utf-8")

    # 依交易年份合併進年度檔（編號相同 → 以新資料覆蓋）
    # 只收 MIN_YEAR～今年：原始資料有日期打錯的（民國 70、119 年之類），不收
    this_year = now.year - 1911
    today = this_year * 10000 + now.month * 100 + now.day
    for kind in ("A", "B"):
        by_year = {}
        for r in incoming[kind]:
            y = r["dt"] // 10000
            if MIN_YEAR <= y <= this_year and r["dt"] <= today:
                by_year.setdefault(y, []).append(r)
        for y, rows in by_year.items():
            p = out / f"{kind}_{y}.json"
            store = load_year(p)
            for r in rows:
                store[r["id"]] = r
            dump(p, sorted(store.values(), key=lambda r: -r["dt"]))

    # 年度檔重寫「本期新增」旗標與「社區簡稱」（成屋，依門牌對照表；對不到就不放），並組近 6 個月
    communities, tx_names = load_communities()
    cut_y, cut_m = (now.year - 1911, now.month - 6) if now.month > 6 else (now.year - 1912, now.month + 6)
    cutoff = cut_y * 10000 + cut_m * 100 + now.day
    recent = {"A": [], "B": []}
    years = {"A": [], "B": []}
    for kind in ("A", "B"):
        for p in sorted(out.glob(f"{kind}_[0-9]*.json")):
            y = int(p.stem.split("_")[1])
            rows = json.loads(p.read_text(encoding="utf-8"))
            changed = False
            for r in rows:
                nw = r["id"] in current_ids[kind]
                if bool(r.get("nw")) != nw:
                    changed = True
                    if nw:
                        r["nw"] = 1
                    else:
                        r.pop("nw", None)
                if kind == "A":
                    tk = tx_key(r["a"], r["dt"])
                    cm = tx_names[tk] if tk in tx_names else communities.get(addr_key(r["a"]) or "")
                    if r.get("cm") != cm:
                        changed = True
                        if cm:
                            r["cm"] = cm
                        else:
                            r.pop("cm", None)
            if changed:
                dump(p, rows)
            years[kind].append(y)
            recent[kind] += [r for r in rows if r["dt"] >= cutoff]
        recent[kind].sort(key=lambda r: -r["dt"])
        dump(out / f"{kind}_recent.json", recent[kind])

    # 各區近 6 個月摘要（中位數排除有備註＝特殊交易、預售排除解約）
    summary = []
    for d in DISTRICTS:
        dn = d[:-1]
        a = [r for r in recent["A"] if r["d"] == dn]
        b = [r for r in recent["B"] if r["d"] == dn]
        clean_a = [r for r in a if not r.get("n")]
        bld = [r["u"] for r in clean_a if r["ty"] in ("大樓", "華廈")]
        house = [r["u"] for r in clean_a if r["k"] == "h"]
        pre = [r["u"] for r in b if not r.get("n") and not r.get("x") and r["k"] == "b"]
        summary.append({
            "d": dn, "countA": len(a), "countB": len(b),
            "bld": median(bld), "bldN": len([v for v in bld if v]),
            "house": median(house), "houseN": len([v for v in house if v]),
            "pre": median(pre), "preN": len([v for v in pre if v]),
        })

    meta = {
        "synced": now.strftime("%Y-%m-%d %H:%M"),
        "release": release_date(now),
        "seasons": seasons if not args.start else [seasons[0], seasons[-1]],
        "cutoff": cutoff,
        "newA": len(current_ids["A"]),
        "newB": len(current_ids["B"]),
        "years": sorted(set(years["A"]) | set(years["B"]), reverse=True),
        "districts": [d[:-1] for d in DISTRICTS],
        "summary": summary,
        "source": "內政部不動產交易實價查詢服務網（開放資料）",
    }
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"✅ 近6個月：成屋 {len(recent['A'])} 筆、預售 {len(recent['B'])} 筆；本期新增 成屋 {meta['newA']}／預售 {meta['newB']}")


if __name__ == "__main__":
    main()
