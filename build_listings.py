"""抓有巢氏沙鹿屏西睦誠加盟店的全部物件 → listings.json

來源：shop.u-trust.com.tw 店頭網頁背後的公開介面 /api/v1/CaseList（非官方，改版就可能失效）。
保護：抓到的筆數是 0，或比上次少一半以上，就不覆蓋舊檔並以錯誤結束（避免介面壞掉把網站洗成空的）。

用法：python build_listings.py --out ../website-v2/data
"""
import argparse
import json
import ssl
import sys
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

SHOP = "0426357766"
API = ("https://shop.u-trust.com.tw/api/v1/CaseList?purposeName=0&pinSearchType=0&caseTypeName=0"
       "&isDownRatio=false&isIStagingUrl=false&isNewCase=false&order=0&page={page}&pageSize=2000"
       "&ShopIndex=" + SHOP + "&channel=Tc")
TW = timezone(timedelta(hours=8))
# 仍驗證憑證鏈；只關掉 Python 3.13 的嚴格格式檢查（u-trust 憑證缺 Subject Key Identifier 會被擋）
SSL_CTX = ssl.create_default_context()
SSL_CTX.verify_flags &= ~ssl.VERIFY_X509_STRICT


def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (liwen-site-data)"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60, context=SSL_CTX) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception:
            if attempt == 2:
                raise
            time.sleep(3)


def num(v):
    if v in (None, ""):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return int(f) if f == int(f) else round(f, 2)


def rooms(c):
    r, l, b = num(c.get("room")), num(c.get("livingRoom")), num(c.get("bathRoom"))
    if not r and not l and not b:
        return ""
    s = f"{r or 0}房{l or 0}廳{b or 0}衛"
    ar = num(c.get("addRoom"))
    if ar:
        s += f"+{ar}房"
    return s


def floor(c):
    f, t, up = num(c.get("fromFloor")), num(c.get("toFloor")), num(c.get("upFloor"))
    if not up:
        return ""
    if not f:
        return f"共{up}樓"
    span = f"{f}" if not t or t == f else f"{f}~{t}"
    return f"{span}/{up}樓"


def normalize(c):
    cover = c.get("cover") or ""
    if cover.startswith("//"):
        cover = "https:" + cover
    cover = cover.replace("{0}", "800").replace("{1}", "600")
    des = (c.get("caseDes") or "").replace("\r", "").strip()
    return {
        "id": c.get("caseSID"),
        "name": (c.get("caseName") or "").strip(),
        "city": c.get("county") or "",
        "dist": c.get("district") or "",
        "addr": c.get("address") or "",
        "price": num(c.get("price")),           # 萬
        "lastPrice": num(c.get("lastPrice")),   # 降價前（萬）
        "type": c.get("caseTypeName") or "",
        "purpose": c.get("purposeName") or "",
        "reg": num(c.get("regArea")),           # 登記坪數
        "main": num(c.get("mainArea")),         # 主建物
        "land": num(c.get("landPin")),          # 地坪
        "age": num(c.get("buildAge")),
        "rooms": rooms(c),
        "floor": floor(c),
        "parking": c.get("parking") or "",
        "des": des[:400],
        "cover": cover,
        "link": c.get("linkUrl") or f"https://buy.u-trust.com.tw/house/{c.get('caseSID')}",
        "vr": c.get("iStagingUrl") or "",
        "down": bool(c.get("isDownRatio")),
    }


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="輸出資料夾（會寫 listings.json）")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    target = out / "listings.json"

    # 一次抓完：分小頁時來源排序不穩定，會有重複和漏抓（實測 518 筆分 3 頁只拿到 513 筆）
    first = get_json(API.format(page=1))
    pa = first["data"]["pa"]
    items = list(first["data"]["list"])
    for p in range(2, pa["totalPageCount"] + 1):
        items += get_json(API.format(page=p))["data"]["list"]

    seen, rows = set(), []
    for c in items:
        if c.get("caseSID") in seen:
            continue
        seen.add(c.get("caseSID"))
        rows.append(normalize(c))

    prev = 0
    if target.exists():
        try:
            prev = len(json.loads(target.read_text(encoding="utf-8")).get("items", []))
        except Exception:
            prev = 0
    if not rows or (prev and len(rows) < prev * 0.5):
        print(f"⚠️ 抓到 {len(rows)} 筆（上次 {prev} 筆），疑似來源異常，不覆蓋。", file=sys.stderr)
        sys.exit(1)

    data = {
        "source": f"https://shop.u-trust.com.tw/{SHOP}/list",
        "synced": datetime.now(TW).strftime("%Y-%m-%d %H:%M"),
        "total": len(rows),
        "items": rows,
    }
    target.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"✅ listings.json：{len(rows)} 筆（介面回報 {pa['totalItemCount']} 筆）")


if __name__ == "__main__":
    main()
