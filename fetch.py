"""撤回論文の汚染チェッカー / Retraction Contamination Checker
Retraction Watch Database（Crossref が無料公開）で撤回された論文を、OpenAlex の引用データと突き合わせ、
撤回の後も引用され続けている論文と、それを引用している雑誌・日本の機関を数える。
研究者個人の名前は出さない（論文・雑誌・機関の単位で示す）。"""
import datetime as dt
import os
from collections import defaultdict

from lib import oc

RW_GIT = "https://gitlab.com/crossref/retraction-watch-data.git"   # 毎営業日更新（Crossref公式）
RW_OLD = "https://api.labs.crossref.org/data/retractionwatch"       # 旧配布先（更新停止。予備）
API = "https://api.openalex.org"
MAILTO = os.environ.get("OPENALEX_MAILTO", "arai-sarai-data@proton.me")
TOP = int(os.environ.get("RW_TOP", "1500"))     # 撤回後の引用を数える論文数（被引用数の多い順）
DEEP = int(os.environ.get("RW_DEEP", "400"))    # 引用元の雑誌・日本の機関まで調べる論文数


def oa(path, **p):
    p.setdefault("mailto", MAILTO)
    if os.environ.get("OPENALEX_KEY"):
        p["api_key"] = os.environ["OPENALEX_KEY"]
    return oc.get(API + path, params=p, as_json=True)


def rw_csv():
    """GitLab の公式リポジトリを浅く clone して CSV を読む。失敗したら旧配布先。"""
    import subprocess
    d = oc.RAW / "rw"
    try:
        if (d / ".git").exists():
            subprocess.run(["git", "-C", str(d), "pull", "-q", "--depth", "1"], check=True, timeout=900)
        else:
            subprocess.run(["git", "clone", "-q", "--depth", "1", RW_GIT, str(d)], check=True, timeout=900)
        csvs = sorted(d.glob("*.csv"), key=lambda p: p.stat().st_size, reverse=True)
        oc.check(csvs, "Retraction Watch リポジトリにCSVがありません")
        oc.log("RW csv", csvs[0].name)
        return csvs[0].read_bytes()
    except (subprocess.SubprocessError, OSError) as e:
        # 旧配布先（Labs API）は更新が止まっているため使わない（古い撤回情報を出さない）
        raise oc.DataError("Retraction Watch の取得に失敗: %s" % e)


def parse_date(s):
    s = str(s).strip().split(" ")[0]
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def run():
    df = oc.read_csv(rw_csv())
    c_doi, c_date = oc.col(df, "OriginalPaperDOI"), oc.col(df, "RetractionDate")
    c_nat, c_title = oc.col(df, "RetractionNature"), oc.col(df, "Title")
    c_journal, c_country, c_reason = oc.col(df, "Journal"), oc.col(df, "Country"), oc.col(df, "Reason")
    oc.check(len(df) > 30000, "Retraction Watch の件数が少なすぎます(%d)" % len(df))
    ret = {}
    country = defaultdict(int)
    for r in df.to_dict("records"):
        if "Retraction" not in r[c_nat]:
            continue
        for ctry in str(r[c_country]).split(";"):
            if ctry.strip():
                country[ctry.strip()] += 1
        doi = str(r[c_doi]).strip().lower()
        d = parse_date(r[c_date])
        if not doi or doi in ("unavailable", "0") or not d:
            continue
        ret[doi] = {"doi": doi, "date": d, "title": r[c_title], "journal": r[c_journal],
                    "reason": "; ".join(x.strip("+ ").strip() for x in str(r[c_reason]).split(";") if x.strip())[:200]}
    oc.log("retractions with DOI", len(ret))
    oc.check(len(ret) > 20000, "DOI付きの撤回論文が少なすぎます")

    # OpenAlex で被引用数を取得（50件ずつ）
    dois = list(ret)
    for i in range(0, len(dois), 50):
        chunk = dois[i:i + 50]
        d = oa("/works", filter="doi:" + "|".join(chunk), per_page=50, select="id,doi,cited_by_count")
        for w in d.get("results", []):
            k = (w.get("doi") or "").replace("https://doi.org/", "").lower()
            if k in ret:
                ret[k]["wid"] = w["id"].rsplit("/", 1)[-1]
                ret[k]["cited"] = w.get("cited_by_count") or 0
        if i % 5000 == 0:
            oc.log("openalex lookup", i)
    found = [x for x in ret.values() if x.get("wid")]
    oc.check(len(found) > len(ret) * 0.5, "OpenAlexで見つかる撤回論文が少なすぎます(%d)" % len(found))

    # 撤回から1年以上たった後の引用を数える（出版の遅れを考慮）
    found.sort(key=lambda x: -x["cited"])
    after_rows = []
    journals = defaultdict(int)
    jp_inst = defaultdict(int)
    for n, x in enumerate(found[:TOP]):
        since = x["date"] + dt.timedelta(days=365)
        if since > dt.date.today():
            continue
        f = "cites:%s,from_publication_date:%s" % (x["wid"], since.isoformat())
        cnt = oa("/works", filter=f, per_page=1)["meta"]["count"]
        x["after"] = cnt
        if n < DEEP and cnt:
            for g in oa("/works", filter=f, group_by="primary_location.source.id").get("group_by", []):
                if g["key"] and g["key"] != "unknown":
                    journals[g.get("key_display_name") or g["key"]] += g["count"]
            for g in oa("/works", filter=f + ",authorships.institutions.country_code:JP",
                        group_by="authorships.institutions.id").get("group_by", []):
                if g["key"] and g["key"] != "unknown":
                    jp_inst[g.get("key_display_name") or g["key"]] += g["count"]
        after_rows.append(x)
        if n % 100 == 0:
            oc.log("cites", n)

    rows = sorted([x for x in after_rows if x.get("after")], key=lambda x: -x["after"])
    meth = ("Retraction Watch Database（撤回の種類が Retraction のもの）の論文を OpenAlex で探し、被引用数の多い上位%d本について、"
            "撤回日の1年後以降に出版された論文からの引用を数えました。撤回に触れたうえでの引用（批判的な引用）も含まれます。"
            "OpenAlexの引用データには漏れや重複があり得ます。" % TOP)
    meth_en = ("Papers with RetractionNature 'Retraction' in the Retraction Watch Database were matched to OpenAlex. For the %d most-cited, "
               "we counted citing works published one year or more after the retraction date. This includes citations that mention "
               "the retraction critically. OpenAlex citation data may be incomplete." % TOP)
    R = [{"id": "after", "tab": "撤回後の引用", "tab_en": "Cited after retraction",
          "title": "撤回されたのに引用され続ける論文", "title_en": "Retracted papers that keep getting cited",
          "desc": "撤回から1年以上たってから出た論文に引用された回数。", "desc_en": "Citations from works published 1+ year after retraction.",
          "rows": [{"title": x["title"][:140], "title_url": "https://doi.org/" + x["doi"], "after": x["after"],
                    "cited": x["cited"], "year": x["date"].year, "reason": x["reason"]} for x in rows],
          "columns": [{"key": "title", "label": "論文", "label_en": "Paper", "type": "link"},
                      {"key": "after", "label": "撤回後の引用", "label_en": "Citations after", "type": "num", "bar": True},
                      {"key": "cited", "label": "被引用数（全体）", "label_en": "All citations", "type": "num"},
                      {"key": "year", "label": "撤回年", "label_en": "Retracted", "type": "num"},
                      {"key": "reason", "label": "撤回理由（RW分類）", "label_en": "Reason (RW)"}],
          "method": meth, "method_en": meth_en, "share_label": "title", "share_value": "after"}]
    jr = sorted([{"name": k, "n": v} for k, v in journals.items()], key=lambda x: -x["n"])
    R.append({"id": "journals", "tab": "引用している雑誌", "tab_en": "Citing journals",
              "title": "撤回論文を引用している雑誌", "title_en": "Journals that cite retracted papers",
              "desc": "上位%d本の撤回論文を、撤回後に引用した論文の掲載誌。" % DEEP,
              "desc_en": "Journals publishing works that cited the top %d retracted papers after retraction." % DEEP,
              "rows": jr, "method": meth, "method_en": meth_en, "share_label": "name", "share_value": "n",
              "columns": [{"key": "name", "label": "雑誌", "label_en": "Journal"},
                          {"key": "n", "label": "撤回後の引用", "label_en": "Citations after retraction", "type": "num", "bar": True}]})
    if jp_inst:
        ir = sorted([{"name": k, "n": v} for k, v in jp_inst.items()], key=lambda x: -x["n"])
        R.append({"id": "jp", "tab": "日本の機関", "tab_en": "Japanese institutions",
                  "title": "撤回論文を撤回後に引用した日本の機関", "title_en": "Japanese institutions citing retracted papers",
                  "desc": "所属機関ごとの延べ数（共著者が複数いる場合はそれぞれ数える）。",
                  "desc_en": "Counted per institution (co-authored works count for each institution).",
                  "rows": ir, "method": meth, "method_en": meth_en, "share_label": "name", "share_value": "n",
                  "columns": [{"key": "name", "label": "機関", "label_en": "Institution"},
                              {"key": "n", "label": "撤回後の引用", "label_en": "Citations after retraction", "type": "num", "bar": True}]})
    cr = sorted([{"name": k, "n": v} for k, v in country.items()], key=lambda x: -x["n"])
    R.append({"id": "country", "tab": "国別", "tab_en": "By country", "title": "撤回論文が多い国（著者の国）",
              "title_en": "Countries with the most retractions (author countries)",
              "desc": "Retraction Watch に記録された撤回件数。研究の量が多い国ほど多くなる点に注意。",
              "desc_en": "Retractions recorded in Retraction Watch. Countries with more research output naturally have more.",
              "rows": cr, "method": meth, "method_en": meth_en, "share_label": "name", "share_value": "n",
              "columns": [{"key": "name", "label": "国", "label_en": "Country"},
                          {"key": "n", "label": "撤回件数", "label_en": "Retractions", "type": "num", "bar": True}]})
    search = [{"n": x["title"][:160], "s": "撤回 %d年 / Retracted %d" % (x["date"].year, x["date"].year),
               "a": x["doi"] + " " + str(x["journal"]),
               "k": [["DOI", x["doi"]], ["雑誌 Journal", x["journal"]], ["理由 Reason", x["reason"][:80]]],
               "u": "https://doi.org/" + x["doi"]} for x in sorted(ret.values(), key=lambda x: -(x.get("cited") or 0))[:30000]]
    tot_after = sum(x.get("after", 0) for x in after_rows)
    meta = {"data_period": "Retraction Watch %s" % oc.today(),
            "stats": [{"label": "記録された撤回論文", "label_en": "Retracted papers on record", "value": len(ret)},
                      {"label": "上位%d本が撤回後に受けた引用" % TOP, "label_en": "Post-retraction citations (top %d)" % TOP,
                       "value": tot_after}],
            "sources": [{"name": "Retraction Watch Database", "org": "Crossref / The Center for Scientific Integrity",
                         "url": "https://gitlab.com/crossref/retraction-watch-data",
                         "license": "CC BY 4.0（Crossref）", "retrieved": oc.today()},
                        {"name": "OpenAlex", "org": "OurResearch", "url": "https://openalex.org/", "license": "CC0",
                         "retrieved": oc.today()}]}
    oc.write(meta, R, search=search)


if __name__ == "__main__":
    oc.main(run)
