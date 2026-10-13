"""官網匯出的 Excel → community_names.json（門牌 → 社區簡稱 對照表）

社區簡稱只出現在內政部官網查詢結果，開放資料沒有、官網也擋程式抓取。
所以由俐文在官網查詢 →「匯出 Excel」→ 用這支匯入。社區簡稱跟著門牌（到「號」）走，
同一棟樓以後的新成交會自動帶上，官網空白的我們也空白。

用法（可一次丟多個檔，會合併進既有對照表）：
  python import_community.py 下載的檔案1.xlsx 檔案2.xlsx
"""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from build_lvr import addr_key, tx_key  # noqa: E402

import openpyxl  # noqa: E402

OUT = Path(__file__).parent / "community_names.json"


def read_export(path):
    ws = openpyxl.load_workbook(path, read_only=True).worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    head = next(i for i, r in enumerate(rows) if r and r[0] == "地段位置或門牌")
    cols = rows[head]
    ia, ic, idt = cols.index("地段位置或門牌"), cols.index("社區簡稱"), cols.index("交易日期")
    for r in rows[head + 1:]:
        if r and r[ia]:
            y, m, d = str(r[idt]).split("/")
            yield str(r[ia]), (str(r[ic]).strip() if r[ic] else ""), int(y) * 10000 + int(m) * 100 + int(d)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    data = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {"map": {}}
    data.setdefault("tx", {})
    votes = defaultdict(Counter)
    seen = []
    n = 0
    for f in sys.argv[1:]:
        for addr, name, dt in read_export(f):
            n += 1
            seen.append((addr, name, dt))
            k = addr_key(addr)
            if k and name:
                votes[k][name] += 1
    added = updated = 0
    for k, c in votes.items():
        name, _ = c.most_common(1)[0]
        if len(c) > 1:
            print(f"⚠️ 同門牌不同社區名：{k} {dict(c)} → 用 {name}")
        old = data["map"].get(k)
        if old is None:
            added += 1
        elif old != name:
            updated += 1
            print(f"  更新：{k} {old} → {name}")
        data["map"][k] = name
    # 逐筆例外：官網這一筆的社區名跟整棟不同（多半是官網這戶空白）→ 照官網這一筆
    for addr, name, dt in seen:
        tk = tx_key(addr, dt)
        if data["map"].get(addr_key(addr) or "", "") != name:
            data["tx"][tk] = name
        else:
            data["tx"].pop(tk, None)
    data["map"] = dict(sorted(data["map"].items()))
    data["tx"] = dict(sorted(data["tx"].items()))
    print(f"  逐筆例外 {len(data['tx'])} 筆（官網這一筆與整棟社區名不同）")
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"✅ 讀 {n} 筆；新增 {added} 個門牌、更新 {updated} 個；對照表共 {len(data['map'])} 個門牌、"
          f"{len(set(data['map'].values()))} 個社區")


if __name__ == "__main__":
    main()
