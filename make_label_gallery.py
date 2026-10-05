"""
サムネイルを一覧で見ながら、ボタンで正解を記入できるページ（HTML）を作る

Excelで1行ずつ確認するより速く判断できるように、
視聴者がクリック前に見る「サムネイル＋タイトル」を並べて表示する。

使い方:
  python make_label_gallery.py              # 最新の labeling_*.xlsx の上から45本
  python make_label_gallery.py --limit 30   # 本数を変える

できたHTMLをダブルクリックするとブラウザで開く。
  - 記入した内容はブラウザに自動で保存される（途中で閉じても続きから再開できる）
  - 最後に「結果をダウンロード」で labels_<日時>.csv を保存する
  - evaluate_validation.py は、ダウンロードフォルダか data/validation/ にあるそのCSVを自動で使う

並び順は labeling_*.xlsx と同じ（作るときにシャッフル済み）なので、
上から一部だけ記入しても、ランダムに選んだサンプルになる。
分類の結果は、このページにも載せない。
"""

import argparse
import html
import json
import re
import sys

import pandas as pd

from make_validation_sample import LANG_CHOICES, THEME_CHOICES
from region_config import DATA_ROOT

PAGE = """<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>正解の記入（__STAMP__）</title>
<style>
  :root {
    --fog: #eef1f3;
    --paper: #ffffff;
    --ink: #1c2b36;
    --muted: #5d6b75;
    --line: #d5dce1;
    --sea: #17635a;
    --sea-soft: #e3f0ed;
    --stone: #5f6368;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--fog); color: var(--ink);
    font-family: "Hiragino Sans", "Yu Gothic UI", "Meiryo", system-ui, sans-serif;
    font-size: 15px; line-height: 1.6;
  }
  header {
    position: sticky; top: 0; z-index: 10;
    display: flex; flex-wrap: wrap; align-items: center; gap: 12px 20px;
    padding: 12px 24px; background: var(--paper); border-bottom: 1px solid var(--line);
  }
  header h1 { font-size: 17px; margin: 0; font-weight: 700; }
  .progress { font-variant-numeric: tabular-nums; color: var(--muted); }
  .progress strong { color: var(--ink); font-size: 18px; }
  .bar { flex: 1 1 160px; height: 6px; background: var(--line); border-radius: 3px; overflow: hidden; min-width: 120px; }
  .bar span { display: block; height: 100%; width: 0; background: var(--sea); transition: width .2s; }
  header label { color: var(--muted); display: flex; gap: 6px; align-items: center; cursor: pointer; }
  .download {
    background: var(--sea); color: #fff; border: 0; border-radius: 6px;
    padding: 8px 16px; font: inherit; font-weight: 700; cursor: pointer;
  }
  .help { padding: 12px 24px 0; color: var(--muted); max-width: 960px; }
  main {
    display: grid; gap: 20px; padding: 16px 24px 48px;
    grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  }
  article {
    background: var(--paper); border: 1px solid var(--line); border-radius: 8px; overflow: hidden;
    display: flex; flex-direction: column;
  }
  article.done { border-color: var(--sea); box-shadow: inset 4px 0 0 var(--sea); }
  .thumb { display: block; aspect-ratio: 16 / 9; background: #cfd6db; }
  .thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .body { padding: 12px 14px 14px; display: flex; flex-direction: column; gap: 10px; flex: 1; }
  .meta { color: var(--muted); font-size: 13px; display: flex; justify-content: space-between; gap: 8px; }
  .title { font-weight: 700; overflow-wrap: anywhere; }
  details { color: var(--muted); font-size: 13px; }
  details summary { cursor: pointer; }
  .row { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
  .row > span { width: 3.5em; color: var(--muted); font-size: 13px; }
  .choice {
    border: 1px solid var(--line); background: var(--paper); color: var(--ink);
    border-radius: 6px; padding: 4px 10px; font: inherit; font-size: 14px; cursor: pointer;
  }
  .choice[aria-pressed="true"] { background: var(--sea); border-color: var(--sea); color: #fff; }
  .choice.not[aria-pressed="true"] { background: var(--stone); border-color: var(--stone); }
  select, input[type=text] {
    font: inherit; font-size: 14px; padding: 4px 8px; border: 1px solid var(--line);
    border-radius: 6px; background: var(--paper); color: var(--ink); flex: 1; min-width: 0;
  }
  select:disabled { background: var(--fog); color: var(--muted); }
  button:focus-visible, select:focus-visible, input:focus-visible, a:focus-visible, summary:focus-visible {
    outline: 3px solid #f2b705; outline-offset: 2px;
  }
  .hidden { display: none; }
  @media (prefers-reduced-motion: reduce) { .bar span { transition: none; } }
</style>
</head>
<body>
<header>
  <h1>正解の記入</h1>
  <div class="progress">記入済み <strong id="done">0</strong> / <span id="total">0</span>本</div>
  <div class="bar" aria-hidden="true"><span id="bar"></span></div>
  <label><input type="checkbox" id="onlyTodo"> 未記入だけ表示</label>
  <button class="download" id="download">結果をダウンロード</button>
</header>
<p class="help">
  サムネイルとタイトルを見て、「何の動画に見えるか」で判断します。観光なら一番の主題のテーマを1つ選び、観光以外ならテーマは空欄のままです。
  言語はタイトルと動画の主な言語です。迷ったら一番近いものを選び、メモに「自信なし」と書いて次へ進んでください。記入内容はこのブラウザに自動で保存されます。
</p>
<main id="list"></main>

<script>
const STAMP = "__STAMP__";
const ROWS = __ROWS__;
const THEMES = __THEMES__;
const LANGS = __LANGS__;
const KEY = "labels_" + STAMP;

let labels = {};
try { labels = JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (e) { labels = {}; }
function save() { try { localStorage.setItem(KEY, JSON.stringify(labels)); } catch (e) {} }

function isDone(no) {
  const l = labels[no] || {};
  if (!l.tourism || !l.lang) return false;
  return l.tourism === "観光以外" || !!l.theme;
}

function el(tag, attrs = {}, children = []) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "text") e.textContent = v; else e.setAttribute(k, v);
  }
  for (const c of children) e.appendChild(c);
  return e;
}

function buttonGroup(no, field, options, extraClass) {
  const wrap = el("div", { class: "row" }, [el("span", { text: field === "tourism" ? "観光" : "言語" })]);
  for (const opt of options) {
    const b = el("button", { class: "choice" + (extraClass && opt === "観光以外" ? " not" : ""), type: "button", text: opt });
    b.setAttribute("aria-pressed", (labels[no] || {})[field] === opt ? "true" : "false");
    b.addEventListener("click", () => {
      labels[no] = labels[no] || {};
      labels[no][field] = opt;
      if (field === "tourism" && opt === "観光以外") labels[no].theme = "";
      save(); render(no);
    });
    wrap.appendChild(b);
  }
  return wrap;
}

function card(r) {
  const no = String(r.no);
  const l = labels[no] || {};
  const a = el("article", { id: "card-" + no });
  if (isDone(no)) a.classList.add("done");

  const link = el("a", { class: "thumb", href: r.url, target: "_blank", rel: "noopener", title: "動画を開く" });
  link.appendChild(el("img", { src: "https://i.ytimg.com/vi/" + r.vid + "/hqdefault.jpg", alt: "", loading: "lazy" }));
  a.appendChild(link);

  const body = el("div", { class: "body" });
  body.appendChild(el("div", { class: "meta" }, [el("span", { text: "#" + no + "　" + r.region }), el("span", { text: r.channel })]));
  body.appendChild(el("div", { class: "title", text: r.title }));
  if (r.desc) {
    const d = el("details");
    d.appendChild(el("summary", { text: "説明文" }));
    d.appendChild(el("div", { text: r.desc }));
    body.appendChild(d);
  }
  body.appendChild(buttonGroup(no, "tourism", ["観光", "観光以外"], true));

  const themeRow = el("div", { class: "row" }, [el("span", { text: "テーマ" })]);
  const sel = el("select", { "aria-label": "テーマ #" + no });
  sel.appendChild(el("option", { value: "", text: "選ぶ" }));
  for (const t of THEMES) {
    const o = el("option", { value: t, text: t });
    if (l.theme === t) o.selected = true;
    sel.appendChild(o);
  }
  sel.disabled = l.tourism === "観光以外";
  sel.addEventListener("change", () => { labels[no] = labels[no] || {}; labels[no].theme = sel.value; save(); refresh(no); });
  themeRow.appendChild(sel);
  body.appendChild(themeRow);

  body.appendChild(buttonGroup(no, "lang", LANGS, false));

  const memoRow = el("div", { class: "row" }, [el("span", { text: "メモ" })]);
  const memo = el("input", { type: "text", placeholder: "迷ったときだけ", "aria-label": "メモ #" + no });
  memo.value = l.memo || "";
  memo.addEventListener("input", () => { labels[no] = labels[no] || {}; labels[no].memo = memo.value; save(); });
  memoRow.appendChild(memo);
  body.appendChild(memoRow);

  a.appendChild(body);
  return a;
}

function render(no) {
  const old = document.getElementById("card-" + no);
  const r = ROWS.find(x => String(x.no) === String(no));
  old.replaceWith(card(r));
  refresh(no);
}

function refresh(no) {
  const c = document.getElementById("card-" + no);
  if (c) c.classList.toggle("done", isDone(String(no)));
  const done = ROWS.filter(r => isDone(String(r.no))).length;
  document.getElementById("done").textContent = done;
  document.getElementById("bar").style.width = (100 * done / ROWS.length) + "%";
  applyFilter();
}

function applyFilter() {
  const only = document.getElementById("onlyTodo").checked;
  for (const r of ROWS) {
    const c = document.getElementById("card-" + r.no);
    if (c) c.classList.toggle("hidden", only && isDone(String(r.no)));
  }
}

function download() {
  const head = ["番号", "正解_観光関連か", "正解_テーマ", "正解_言語", "メモ"];
  const esc = v => '"' + String(v ?? "").replace(/"/g, '""') + '"';
  const lines = [head.join(",")];
  for (const r of ROWS) {
    const l = labels[String(r.no)];
    if (!l || !l.tourism) continue;
    lines.push([r.no, l.tourism, l.theme || "", l.lang || "", l.memo || ""].map(esc).join(","));
  }
  const blob = new Blob(["\\ufeff" + lines.join("\\r\\n")], { type: "text/csv" });
  const a = el("a", { href: URL.createObjectURL(blob), download: "labels_" + STAMP + ".csv" });
  document.body.appendChild(a); a.click(); a.remove();
}

document.getElementById("total").textContent = ROWS.length;
const list = document.getElementById("list");
for (const r of ROWS) list.appendChild(card(r));
document.getElementById("onlyTodo").addEventListener("change", applyFilter);
document.getElementById("download").addEventListener("click", download);
refresh(ROWS[0].no);
</script>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=45, help="上から何本を記入対象にするか")
    args = parser.parse_args()

    vdir = DATA_ROOT / "validation"
    files = sorted(vdir.glob("labeling_*.xlsx"))
    if not files:
        sys.exit("labeling_*.xlsx がありません。先に make_validation_sample.py を実行してください。")
    xlsx = files[-1]
    stamp = xlsx.stem.replace("labeling_", "")

    df = pd.read_excel(xlsx, sheet_name="ラベル付け")
    df = df[df["番号"].astype(str) != "例"].head(args.limit)
    rows = []
    for _, r in df.iterrows():
        url = str(r["動画URL"])
        m = re.search(r"v=([\w-]{11})", url)
        rows.append({
            "no": int(r["番号"]), "region": str(r["地域"]), "title": str(r["タイトル"]),
            "desc": "" if pd.isna(r["説明文（冒頭）"]) else str(r["説明文（冒頭）"]),
            "channel": "" if pd.isna(r["チャンネル"]) else str(r["チャンネル"]),
            "url": url, "vid": m.group(1) if m else "",
        })

    def js(obj):
        # </script> でスクリプトが途切れないようにする
        return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")

    page = (PAGE.replace("__STAMP__", html.escape(stamp))
                .replace("__ROWS__", js(rows))
                .replace("__THEMES__", js(THEME_CHOICES))
                .replace("__LANGS__", js(LANG_CHOICES)))
    out = vdir / f"labeling_{stamp}.html"
    out.write_text(page, encoding="utf-8")
    print(f"{len(rows)}本の記入ページを作りました: {out}")
    print("ダブルクリックしてブラウザで開いてください。終わったら「結果をダウンロード」を押します。")


if __name__ == "__main__":
    main()
