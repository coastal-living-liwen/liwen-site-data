# site-data — 個人網站「在售物件」「實價登錄」的自動更新資料

網站（Cloudflare，手動上傳 website-v2.zip）本身是靜態的，自己不會更新。
這個資料夾放到 GitHub 後，GitHub 每天自動跑兩次程式、更新 `data/`，
網站的 `listings.html`、`lvr.html`、首頁物件區再去讀 GitHub Pages 上的最新資料。

```
有巢氏店頭介面 ─┐
                ├─ GitHub Actions（每天 06:00、12:00）→ data/*.json → GitHub Pages
內政部開放資料 ─┘                                                        ↓
                                     coastal-living-liwen.com 的頁面用 fetch 讀取
```

## 檔案

| 檔案 | 做什麼 |
|---|---|
| `build_listings.py` | 抓有巢氏沙鹿屏西睦誠加盟店全店物件 → `data/listings.json` |
| `build_lvr.py` | 抓內政部實價登錄（沙鹿、梧棲、清水、龍井、西屯）→ `data/lvr/` |
| `import_community.py` | 官網匯出的 Excel → `community_names.json`（門牌 → 社區簡稱） |
| `community_names.json` | 社區簡稱對照表；build_lvr.py 每次都會依它替成屋標上 `cm` |
| `.github/workflows/update.yml` | 排程設定 |
| `.nojekyll` | 讓 GitHub Pages 原樣提供檔案 |

## 資料來源與保護

- **物件**：`shop.u-trust.com.tw/api/v1/CaseList`，非官方介面，有巢氏改版就可能失效。
  抓到 0 筆或比上次少一半以上 → 不覆蓋舊資料、工作標失敗，GitHub 會寄信通知。
  失敗期間網站會繼續顯示最後一次成功的資料。
- **實價登錄**：內政部開放資料（政府資料開放授權條款）。每月 1、11、21 日發布。
  平常只抓最近 2 季＋本期，用「編號」去重合併進年度檔，不會重複累加。
  只收 104 年到今天的交易（原始資料有打錯日期的）。

## 第一次設定（俐文在 GitHub 網頁上操作）

1. 建一個**公開**的 repository，名稱 `liwen-site-data`。
2. 把這個資料夾的全部內容上傳（含 `data/`、`.github/`、`.nojekyll`）。
3. Settings → Pages → Source 選「Deploy from a branch」，Branch 選 `main`、`/ (root)`。
4. Settings → Actions → General → Workflow permissions 選「Read and write permissions」。
5. Actions → 「更新在售物件與實價登錄」→ Run workflow，跑一次確認成功（綠勾）。
6. 把 `website-v2/data-config.js` 改成
   `window.LIWEN_DATA_BASE = 'https://<帳號>.github.io/liwen-site-data/data/';`
   重新打包 website-v2.zip、上傳 Cloudflare。這一步之後網站就會自動更新。

## 本機手動更新（不需要 GitHub 也能用）

```bash
python build_listings.py --out ../website-v2/data
python build_lvr.py --out ../website-v2/data
```

第一次建歷史資料（約 47 季，下載約 200MB，用快取資料夾避免重抓）：

```bash
python build_lvr.py --out ../website-v2/data --from 104S1 --cache %TEMP%\lvrcache
```

## 社區簡稱（成屋）

開放資料沒有社區名稱；內政部官網查詢結果有「社區簡稱」，但官網用 token、加密參數、亂序字型擋程式抓，**不要去破解**。
做法：俐文在官網查詢 → 匯出 Excel → `python import_community.py 檔案.xlsx` → 重跑 build_lvr.py。
對照 key 是門牌到「號」（例 `梧棲區|八德東路65號`），同一棟樓以後的新成交自動帶上；官網空白的不標。
逐筆例外 `tx`：官網那一筆的社區名跟整棟不同（多半是官網那戶空白）→ 照官網那一筆，做到「官網沒有我們就沒有」。
官網一次最多匯出 1500 筆，從最新往回取，期間開頭會被截掉；要更早的就分段匯出。
驗證（10/13，五區各 1500 筆）：我們有的交易 7,440 筆社區名與官網 100% 一致；另有 60 筆官網查得到、
開放資料（季檔＋本期＋歷史批）都沒有，原因內政部未說明。

## 踩過的坑

- **Python 3.13 擋憑證**：u-trust 和內政部的憑證缺 Subject Key Identifier，3.13 預設嚴格檢查會失敗。
  程式仍驗證憑證鏈，只關掉 `VERIFY_X509_STRICT`。
- **物件分頁會漏抓**：pageSize=200 分 3 頁抓，518 筆只拿到 513 筆（來源排序不穩定）。改成一次抓 2000 筆。
- **CSV 欄位超長**：110S4 預售檔有超過 128KB 的欄位，要放大 `csv.field_size_limit`。
- **季檔和本期中間有空窗**：本期只留最新一批，季檔到季末才出，中間的批次（例：10/1 那批）只在
  `DownloadHistory?type=history&fileName=YYYYMMDD`。沒抓的話 10/13 會漏 162 筆（梧棲 8 月好幾筆）。
  現在每次都會抓還沒處理過的歷史批，處理過的記在 `data/lvr/releases_done.json`。
- **本期新增某些區是 0 筆**是正常的：內政部每一批的資料分布不平均（例：115/10/11 那批梧棲、清水都是 0 筆）。
- **單價**採內政部公布值。車位沒有單獨計價時，單價包含車位面積與價格，會偏低。頁面上有註明。
