#!/usr/bin/env python3
"""扫描报告目录，把每份报告登记到 skill_reports（每公司每 skill 取最新日期）。

目录约定：项目下 report/报告_<skill名>/ 存放该 skill 生成的报告
文件命名约定（新）：
    {股票名}_{skill名}_研究报告_{YYYYMMDD}.md   例：CEG_earnings-review_研究报告_20260808.md
遗留命名（旧）：
    {股票名}投资研究报告_{YYYYMMDD}.md         例：CEG投资研究报告_20260808.md（skill 取自文件夹名）

"取最新"规则：同一 (公司, skill) 有多份报告时，保留 latest_report_date 最大的那份。

用法：
    python3 scripts/ingest_reports.py            # 全量扫描
"""
from pathlib import Path
import re
import sqlite3

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "research.db"

# 报告文件名中的"股票名" → 标准 ticker（遗留/非标准名归一化）
NORMALIZE = {
    "AppLovin": "APP",
    "Oracle": "ORCL",
}

# 旧格式: CEG投资研究报告_20260808.md
RE_OLD = re.compile(r"^(?P<ticker>.+?)投资研究报告_(?P<date>\d{8})\.md$")
# 新格式: CEG_earnings-review_研究报告_20260808.md
RE_NEW = re.compile(r"^(?P<ticker>.+?)_(?P<skill>.+?)_研究报告_(?P<date>\d{8})\.md$")


def parse_filename(fname: str, folder_skill: str):
    """返回 (ticker, skill, date) 或 None（非单公司报告，如汇总表）。"""
    m = RE_NEW.match(fname)
    if m:
        ticker = NORMALIZE.get(m.group("ticker"), m.group("ticker"))
        return ticker, m.group("skill"), m.group("date")
    m = RE_OLD.match(fname)
    if m:
        ticker = NORMALIZE.get(m.group("ticker"), m.group("ticker"))
        return ticker, folder_skill, m.group("date")
    return None


def ensure_company(conn, ticker):
    """确保公司存在，缺则插入最小记录（市值待后续抓取）。"""
    cur = conn.execute("SELECT id FROM companies WHERE ticker=?", (ticker,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur = conn.execute(
        "INSERT INTO companies (ticker, company_name) VALUES (?, ?)",
        (ticker, ticker),
    )
    return cur.lastrowid


def upsert_report(conn, company_id, skill, date, rel_path):
    """每(公司,skill)保留最新日期；新日期更大才覆盖。"""
    conn.execute(
        """INSERT INTO skill_reports (company_id, skill, latest_report_date, report_path)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(company_id, skill) DO UPDATE SET
             latest_report_date = CASE
               WHEN excluded.latest_report_date > skill_reports.latest_report_date
                 THEN excluded.latest_report_date ELSE skill_reports.latest_report_date END,
             report_path = CASE
               WHEN excluded.latest_report_date > skill_reports.latest_report_date
                 THEN excluded.report_path ELSE skill_reports.report_path END,
             updated_at = datetime('now','localtime')""",
        (company_id, skill, date, rel_path),
    )


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    count = 0
    skipped = []

    report_root = PROJECT_ROOT / "report"
    if not report_root.is_dir():
        print(f"未找到报告根目录: {report_root}（应先创建 report/ 并把 报告_* 放进去）")
        conn.close()
        return

    for folder in sorted(report_root.iterdir()):
        if not (folder.is_dir() and folder.name.startswith("报告_")):
            continue
        skill = folder.name[len("报告_"):]
        for f in sorted(folder.glob("*.md")):
            parsed = parse_filename(f.name, skill)
            if not parsed:
                skipped.append(f.relative_to(PROJECT_ROOT).as_posix())
                continue
            ticker, rskill, rdate = parsed
            rel = f.relative_to(PROJECT_ROOT).as_posix()
            cid = ensure_company(conn, ticker)
            upsert_report(conn, cid, rskill, rdate, rel)
            count += 1
            print(f"  [{rskill:>16}] {ticker:<10} {rdate}  {rel}")

    conn.commit()

    print(f"\n登记报告 {count} 份。")
    if skipped:
        print(f"跳过（非单公司报告）{len(skipped)} 份：")
        for s in skipped:
            print(f"  - {s}")

    print("\n=== 当前 skill_reports 跟踪（每公司每 skill 最新） ===")
    rows = conn.execute(
        """SELECT c.ticker, c.company_name, c.market_cap_b, s.skill, s.latest_report_date, s.report_path
           FROM skill_reports s JOIN companies c ON c.id = s.company_id
           ORDER BY c.ticker, s.skill"""
    ).fetchall()
    for r in rows:
        mcap = f"{r[2]:.1f}B" if r[2] is not None else "   -"
        print(f"  {r[0]:<10} {r[1][:28]:<28} {mcap:>8}  {r[3]:<16} {r[4]}")
    conn.close()


if __name__ == "__main__":
    main()
