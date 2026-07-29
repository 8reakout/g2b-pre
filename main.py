from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import yaml
from dotenv import load_dotenv

from email_notifier import send_html_email
from g2b_prespec_fetch import PrespecNotice, fetch_g2b_prespecs


BASE_DIR = Path(__file__).resolve().parent


def load_config() -> dict[str, Any]:
    config_path = BASE_DIR / "config.yaml"
    if not config_path.exists():
        config_path = BASE_DIR / "config.example.yaml"

    with config_path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_seen_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return set()
    if isinstance(data, list):
        return {str(x) for x in data}
    return set()


def save_seen_ids(path: Path, ids: set[str]) -> None:
    path.write_text(json.dumps(sorted(ids), ensure_ascii=False, indent=2), encoding="utf-8")


def save_latest(path: Path, prespecs: list[PrespecNotice]) -> None:
    path.write_text(
        json.dumps([n.to_dict() for n in prespecs], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _e(value: str) -> str:
    return html.escape(value or "")


def _link(url: str, label: str = "규격서") -> str:
    if not url:
        return "-"
    return f'<a href="{_e(url)}" target="_blank">{_e(label)}</a>'


def _prespec_page_link(n: PrespecNotice) -> str:
    """
    나라장터는 WebSquare 기반 SPA라 사전규격 상세화면의 고정 URL이 API 응답에
    직접 제공되지 않습니다. 따라서 사전규격등록번호를 기준으로 나라장터 통합검색
    화면으로 이동시키는 링크를 생성합니다.

    링크가 바로 상세화면을 열지 못하는 환경에서는 나라장터 화면에서
    사전규격등록번호 또는 품명/사업명으로 다시 검색하면 됩니다.
    """
    query = (n.notice_id or n.ref_no or n.title or "").strip()
    if not query:
        return n.spec_file_url or ""

    return "https://www.g2b.go.kr#FIUA002_01?keyword=" + quote(query, safe="")


def build_html(prespecs: list[PrespecNotice], new_ids: set[str], config: dict[str, Any]) -> str:
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    keywords = ", ".join(config["g2b_prespec"].get("keywords", [])) or "전체"
    new_count = sum(1 for n in prespecs if n.notice_id in new_ids)

    rows = []
    for idx, n in enumerate(prespecs, start=1):
        title = _e(n.title)
        page_url = _prespec_page_link(n)
        if page_url:
            title = f'<a href="{_e(page_url)}" target="_blank">{title}</a>'

        org = n.demand_org or n.order_org or "-"
        opinion_date = n.opinion_close_date or "-"
        registered_date = n.registered_date or n.receipt_date or "-"

        rows.append(
            f"""
            <tr>
              <td class="num">{idx}</td>
              <td><div class="title">{title}</div></td>
              <td class="org">{_e(org)}</td>
              <td class="opinion-date">{_e(opinion_date)}</td>
              <td class="registered-date">{_e(registered_date[:10])}</td>
              <td class="budget">{_e(n.budget_amount or '-')}</td>
              <td class="spec-link">{_link(n.spec_file_url)}</td>
            </tr>
            """
        )

    if not rows:
        rows.append('<tr><td colspan="7" class="empty">조건에 맞는 나라장터 사전규격 공고가 없습니다.</td></tr>')

    return f"""
<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>나라장터 사전규격 알림</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Malgun Gothic", sans-serif; color:#222; }}
  .wrap {{ max-width: 1200px; margin: 0 auto; }}
  .summary {{ background:#f6f8fa; border:1px solid #d0d7de; border-radius:8px; padding:16px; margin-bottom:16px; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 13px; table-layout: fixed; }}
  th, td {{ border:1px solid #d0d7de; padding:10px 12px; vertical-align: middle; word-break: keep-all; }}
  th {{ background:#f6f8fa; white-space: nowrap; }}
  .num {{ text-align:center; width:45px; white-space: nowrap; }}
  .title {{ font-weight:600; line-height:1.4; }}
  .org {{ width:170px; }}
  .opinion-date {{ width:145px; text-align:center; white-space:nowrap; }}
  .registered-date {{ width:105px; text-align:center; white-space:nowrap; }}
  .budget {{ width:130px; text-align:right; white-space:nowrap; }}
  .spec-link {{ width:70px; text-align:center; white-space:nowrap; }}
  .empty {{ text-align:center; padding:24px; color:#666; }}
</style>
</head>
<body>
<div class="wrap">
  <h2>나라장터 사전규격 알림</h2>
  <div class="summary">
    <div><b>생성일시:</b> {_e(generated_at)}</div>
    <div><b>검색 키워드:</b> {_e(keywords)}</div>
    <div><b>전체 사전규격:</b> {len(prespecs)}건 / <b>신규 사전규격:</b> {new_count}건</div>
    <div style="margin-top:6px;color:#57606a;font-size:12px;">품명/사업명을 클릭하면 나라장터 통합검색 화면으로 이동합니다. 규격서 파일은 우측 "규격서" 링크에서 확인할 수 있습니다.</div>
  </div>
  <table>
    <thead>
      <tr>
        <th>No</th>
        <th>품명/사업명</th>
        <th>수요/발주기관</th>
        <th>의견마감일시</th>
        <th>등록일자</th>
        <th>배정예산</th>
        <th>규격서</th>
      </tr>
    </thead>
    <tbody>
      {''.join(rows)}
    </tbody>
  </table>
</div>
</body>
</html>
"""


def main() -> None:
    load_dotenv(BASE_DIR / ".env")
    config = load_config()

    state_cfg = config.get("state", {})
    seen_file = BASE_DIR / state_cfg.get("seen_file", "seen_prespec_ids.json")
    latest_file = BASE_DIR / state_cfg.get("latest_file", "latest_prespecs.json")
    html_file = BASE_DIR / "g2b_prespec_latest.html"

    seen_ids = load_seen_ids(seen_file)
    prespecs = fetch_g2b_prespecs(config)

    current_ids = {n.notice_id for n in prespecs}
    new_ids = current_ids - seen_ids

    save_latest(latest_file, prespecs)
    html_body = build_html(prespecs, new_ids, config)
    html_file.write_text(html_body, encoding="utf-8")

    subject = f"[나라장터 사전규격] 신규 {len(new_ids)}건 / 전체 {len(prespecs)}건"
    send_html_email(subject, html_body, attachment_path=str(html_file))

    save_seen_ids(seen_file, seen_ids | current_ids)

    print(f"[정보] 신규 사전규격 수: {len(new_ids)}")
    print(f"[정보] 전체 사전규격 수: {len(prespecs)}")
    print(f"[정보] HTML 저장: {html_file}")


if __name__ == "__main__":
    main()