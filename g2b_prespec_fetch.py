from __future__ import annotations

import os
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from html import unescape
from typing import Any

import requests

KST = timezone(timedelta(hours=9))


@dataclass
class PrespecNotice:
    notice_id: str
    title: str
    business_type: str = ""
    ref_no: str = ""
    order_org: str = ""
    demand_org: str = ""
    budget_amount: str = ""
    receipt_date: str = ""
    opinion_close_date: str = ""
    registered_date: str = ""
    sw_business: str = ""
    manager_name: str = ""
    manager_tel: str = ""
    spec_file_url: str = ""
    bid_notice_numbers: str = ""
    keyword: str = ""
    raw: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _today_kst() -> date:
    return datetime.now(KST).date()


def _strip_html(value: str) -> str:
    value = unescape(value or "")
    value = re.sub(r"(?i)<br\s*/?>", " ", value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _first_value(item: dict[str, Any], candidates: list[str]) -> str:
    for key in candidates:
        if key in item and item[key] not in (None, ""):
            return str(item[key]).strip()
    return ""


def _dig_items(data: Any) -> list[dict[str, Any]]:
    """나라장터 JSON 응답에서 item 목록을 유연하게 추출합니다."""
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]

    if not isinstance(data, dict):
        return []

    paths = [
        ["response", "body", "items", "item"],
        ["response", "body", "items"],
        ["body", "items", "item"],
        ["body", "items"],
        ["items", "item"],
        ["items"],
        ["data"],
        ["result"],
        ["list"],
    ]

    for path in paths:
        cur: Any = data
        for key in path:
            if isinstance(cur, dict) and key in cur:
                cur = cur[key]
            else:
                cur = None
                break

        if cur is None:
            continue

        if isinstance(cur, dict):
            return [cur]

        if isinstance(cur, list):
            return [x for x in cur if isinstance(x, dict)]

    return []


def _text_of(parent: ET.Element, path: str) -> str:
    el = parent.find(path)
    if el is None:
        return ""
    return "".join(el.itertext()).strip()


def _parse_xml_items(xml_text: str) -> list[dict[str, Any]]:
    """나라장터 XML 응답에서 body/items/item 목록만 추출합니다."""
    root = ET.fromstring(xml_text)

    result_code = _text_of(root, "./header/resultCode")
    result_msg = _text_of(root, "./header/resultMsg")
    if result_code and result_code not in {"00", "000"}:
        raise RuntimeError(f"G2B 사전규격 API 오류: resultCode={result_code}, resultMsg={result_msg}")

    items: list[dict[str, Any]] = []
    # 일부 예제는 body 바로 아래 item이 있는 것처럼 잘못 표기되어 있어 .//item으로 넓게 탐색합니다.
    for item_el in root.findall(".//item"):
        item: dict[str, Any] = {}
        for child in list(item_el):
            tag = child.tag.split("}")[-1]
            item[tag] = unescape("".join(child.itertext()).strip())
        if item:
            items.append(item)
    return items


def _normalize_date(value: str) -> str:
    value = _strip_html(value)
    if not value:
        return ""

    value = value.replace("T", " ").replace("/", "-").replace(".", "-")
    value = re.sub(r"\s+", " ", value).strip()

    if re.fullmatch(r"\d{12,14}", value):
        return f"{value[:4]}-{value[4:6]}-{value[6:8]} {value[8:10]}:{value[10:12]}"

    if re.fullmatch(r"\d{8}", value):
        return f"{value[:4]}-{value[4:6]}-{value[6:8]}"

    match = re.match(r"(20\d{2})-(\d{1,2})-(\d{1,2})(?:\s+(\d{1,2}):(\d{1,2})(?::\d{1,2})?)?", value)
    if match:
        y, m, d, hh, mm = match.groups()
        date_part = f"{y}-{int(m):02d}-{int(d):02d}"
        if hh and mm:
            return f"{date_part} {int(hh):02d}:{int(mm):02d}"
        return date_part

    return value


def _date_part(value: str) -> date | None:
    value = _normalize_date(value)
    if not value:
        return None
    value = value[:10]
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _format_amount(value: str, business_type: str = "") -> str:
    value = (value or "").strip()
    if not value:
        return ""
    try:
        num = int(float(value.replace(",", "")))
        suffix = "달러" if "외자" in business_type else "원"
        return f"{num:,}{suffix}"
    except ValueError:
        return value


def _contains_keyword(text: str, keywords: list[str]) -> bool:
    keywords = [str(k).strip() for k in keywords if str(k).strip()]
    if not keywords:
        return True
    text_l = (text or "").lower()
    return any(k.lower() in text_l for k in keywords)


def _find_matched_keyword_in_title(title: str, keywords: list[str]) -> str:
    keywords = [str(k).strip() for k in keywords if str(k).strip()]
    if not keywords:
        return ""
    title_l = (title or "").lower()
    for keyword in keywords:
        if keyword.lower() in title_l:
            return keyword
    return ""


TITLE_FIELD_CANDIDATES = [
    # 나라장터 사전규격 API에서 업무구분/버전에 따라 사업명 필드명이 다르게 내려올 수 있어 넓게 대응합니다.
    "prdctClsfcNoNm",
    "bsnsNm",
    "bizNm",
    "bssInfoBizNm",
    "prcrmntReqNoNm",
    "prodNm",
    "prdctNm",
    "prdlstNm",
    "itemNm",
    "servcNm",
    "cntrctNm",
    "bidNtceNm",
    "pblancNm",
    "ntceNm",
    "title",
    "품명",
    "사업명",
    "공고명",
]


def _guess_prespec_title(item: dict[str, Any], keywords: list[str] | None = None) -> str:
    """사전규격 응답에서 품명/사업명 역할의 제목을 최대한 유연하게 찾습니다."""
    title = _strip_html(_first_value(item, TITLE_FIELD_CANDIDATES))
    if title:
        return title

    # 후보명이 문서/버전에 따라 달라질 수 있어 key 이름에 title 성격이 보이면 추가 탐색합니다.
    include_key_tokens = [
        "bsns", "biz", "prdct", "prod", "prdlst", "item", "servc", "service",
        "cntrct", "ntce", "pblanc", "title", "name", "nm", "품명", "사업명", "공고명"
    ]
    exclude_key_tokens = [
        "org", "instt", "dminstt", "order", "ofcl", "tel", "email", "url", "file",
        "date", "dt", "clse", "rgst", "amt", "budget", "bdgt", "no", "cd", "code",
        "기관", "담당", "전화", "메일", "url", "파일", "일시", "금액", "번호", "코드"
    ]

    for key, value in item.items():
        if value in (None, ""):
            continue
        key_l = str(key).lower()
        if any(token in key_l for token in exclude_key_tokens):
            continue
        if any(token in key_l for token in include_key_tokens):
            text = _strip_html(str(value))
            if len(text) >= 3 and not text.startswith("http"):
                return text

    # 그래도 못 찾으면, 키워드가 포함된 긴 문자열을 제목 후보로 사용합니다.
    keywords = [str(k).strip() for k in (keywords or []) if str(k).strip()]
    for value in item.values():
        text = _strip_html(str(value))
        if not text or text.startswith("http") or len(text) < 3:
            continue
        if keywords and any(k.lower() in text.lower() for k in keywords):
            return text

    return ""


def _is_active_prespec(opinion_close_date: str) -> bool:
    close = _date_part(opinion_close_date)
    if close is None:
        # 마감일시가 없으면 판단이 불가하므로 포함합니다.
        return True
    return close >= _today_kst()


def _get_first_spec_file_url(item: dict[str, Any]) -> str:
    for key in ["specDocFileUrl1", "specDocFileUrl2", "specDocFileUrl3", "specDocFileUrl4", "specDocFileUrl5"]:
        value = _first_value(item, [key])
        if value:
            return unescape(value).strip()
    return ""


def request_with_retry(api_url: str, params: dict[str, Any], max_retries: int = 3) -> requests.Response:
    last_error: Exception | None = None

    for attempt in range(1, max_retries + 1):
        try:
            print(f"[정보] 나라장터 사전규격 API 요청 시도: {attempt}/{max_retries}")
            response = requests.get(
                api_url,
                params=params,
                timeout=30,
                headers={
                    "User-Agent": "Mozilla/5.0 GovMonitoringBot/1.0",
                    "Accept": "application/json, application/xml, text/xml, text/plain, */*",
                },
            )
            response.raise_for_status()
            return response
        except requests.exceptions.RequestException as exc:
            last_error = exc
            print(f"[경고] 나라장터 사전규격 API 요청 실패: {attempt}/{max_retries} - {exc}")
            if attempt < max_retries:
                time.sleep(5 * attempt)

    raise RuntimeError(f"나라장터 사전규격 API 요청이 {max_retries}회 모두 실패했습니다: {last_error}")


def _build_params(config: dict[str, Any], page_no: int, service_key_name: str = "ServiceKey") -> dict[str, Any]:
    gcfg = config["g2b_prespec"]
    params_cfg = dict(gcfg.get("params", {}))

    service_key = (
        os.getenv("G2B_PRESPEC_SERVICE_KEY")
        or os.getenv("G2B_SERVICE_KEY")
        or os.getenv("DATA_GO_KR_SERVICE_KEY")
        or gcfg.get("service_key", "")
    ).strip()
    if not service_key:
        raise ValueError("G2B_PRESPEC_SERVICE_KEY 또는 G2B_SERVICE_KEY 값이 없습니다.")

    lookback_days = int(gcfg.get("lookback_days", 30))
    today = _today_kst()
    begin = today - timedelta(days=lookback_days)

    num_rows = int(params_cfg.get("numOfRows", 100))
    if num_rows > 100:
        print(f"[경고] numOfRows={num_rows} 요청은 불안정할 수 있어 100으로 조정합니다.")
        num_rows = 100

    params: dict[str, Any] = {
        service_key_name: service_key,
        "pageNo": page_no,
        "numOfRows": num_rows,
        "type": str(params_cfg.get("type", "json")),
        # 사전규격 기본 목록 조회는 조회구분 1 = 등록일시 기준입니다.
        "inqryDiv": str(params_cfg.get("inqryDiv", "1")),
        "inqryBgnDt": params_cfg.get("inqryBgnDt") or begin.strftime("%Y%m%d0000"),
        "inqryEndDt": params_cfg.get("inqryEndDt") or today.strftime("%Y%m%d2359"),
    }

    # 사용자가 config.yaml에 직접 추가한 기타 검색조건도 보존합니다.
    # 단, 키워드 prdctClsfcNoNm은 API 검색조건에 넣지 않고 Python에서 품명 포함 여부로 필터링합니다.
    skip_keys = {"pageNo", "numOfRows", "type", "inqryDiv", "inqryBgnDt", "inqryEndDt", "ServiceKey", "serviceKey", "prdctClsfcNoNm"}
    for key, value in params_cfg.items():
        if key not in skip_keys and value not in (None, ""):
            params[key] = value

    return params


def _convert_item_to_prespec(
    item: dict[str, Any],
    configured_work_type: str,
    matched_keyword: str,
    config: dict[str, Any],
) -> PrespecNotice | None:
    gcfg = config["g2b_prespec"]
    # 사전규격은 opninRgstClseDt(의견등록마감일시)가 API 실행일보다 이전이면 제외합니다.
    # config.yaml의 active_only가 true일 때 적용됩니다.
    active_only = bool(gcfg.get("active_only", True))
    include_keywords = [str(x).strip() for x in gcfg.get("include_keywords", []) if str(x).strip()]
    exclude_keywords = [str(x).strip() for x in gcfg.get("exclude_keywords", []) if str(x).strip()]

    title = _guess_prespec_title(item, gcfg.get("keywords", []))
    if not title:
        return None

    combined_text = " ".join(str(v) for v in item.values() if v is not None)
    if include_keywords and not _contains_keyword(combined_text, include_keywords):
        return None
    if exclude_keywords and _contains_keyword(combined_text, exclude_keywords):
        return None

    business_type = _strip_html(_first_value(item, ["bsnsDivNm", "businessType", "업무구분명"])) or configured_work_type
    opinion_close_date = _normalize_date(_first_value(item, ["opninRgstClseDt", "opinionCloseDate", "opinionRgstClseDt", "opninClseDt", "의견등록마감일시"]))
    if active_only and not _is_active_prespec(opinion_close_date):
        return None

    prespec_no = _first_value(item, ["bfSpecRgstNo", "preSpecRgstNo", "사전규격등록번호"])
    ref_no = _strip_html(_first_value(item, ["refNo", "참조번호"]))
    notice_id = prespec_no or f"{title}|{ref_no}|{opinion_close_date}"

    registered_date = _normalize_date(_first_value(item, ["rgstDt", "등록일시"]))
    receipt_date = _normalize_date(_first_value(item, ["rcptDt", "접수일시"]))

    return PrespecNotice(
        notice_id=str(notice_id),
        title=title,
        business_type=business_type,
        ref_no=ref_no,
        order_org=_strip_html(_first_value(item, ["orderInsttNm", "ntceInsttNm", "발주기관명", "공고기관명"])),
        demand_org=_strip_html(_first_value(item, ["rlDminsttNm", "dminsttNm", "실수요기관명", "수요기관명"])),
        budget_amount=_format_amount(_first_value(item, ["asignBdgtAmt", "배정예산금액", "배정예산액"]), business_type),
        receipt_date=receipt_date,
        opinion_close_date=opinion_close_date,
        registered_date=registered_date,
        sw_business=_strip_html(_first_value(item, ["swBizObjYn", "SW사업대상여부"])),
        manager_name=_strip_html(_first_value(item, ["ofclNm", "담당자명"])),
        manager_tel=_strip_html(_first_value(item, ["ofclTelNo", "담당자전화번호"])),
        spec_file_url=_get_first_spec_file_url(item),
        bid_notice_numbers=_strip_html(_first_value(item, ["bidNtceNoList", "입찰공고번호목록"])),
        keyword=matched_keyword,
        raw=item,
    )


def _extract_items_from_response(response: requests.Response) -> list[dict[str, Any]]:
    text = response.text.strip()

    if text.startswith("<"):
        return _parse_xml_items(text)

    try:
        data = response.json()
    except Exception as exc:
        raise RuntimeError("나라장터 사전규격 API 응답이 JSON/XML 형식이 아닙니다. 응답 앞부분: " + text[:500]) from exc

    return _dig_items(data)


def fetch_g2b_prespecs(config: dict[str, Any]) -> list[PrespecNotice]:
    """나라장터 사전규격정보 API를 조회하고, 품명/사업명에 키워드가 포함된 항목만 반환합니다."""
    gcfg = config["g2b_prespec"]
    base_url = gcfg.get("api_base_url", "").strip().rstrip("/")
    if not base_url:
        raise ValueError("config.yaml의 g2b_prespec.api_base_url 값이 비어 있습니다.")

    keywords = [str(x).strip() for x in gcfg.get("keywords", []) if str(x).strip()]
    max_pages = int(gcfg.get("max_pages", 10))
    debug_response = bool(gcfg.get("debug_response", False))

    work_types = gcfg.get("work_types", [])
    if not work_types:
        raise ValueError("config.yaml의 g2b_prespec.work_types 값이 비어 있습니다.")

    all_prespecs: list[PrespecNotice] = []

    for work_type in work_types:
        if work_type.get("enabled") is False:
            continue

        work_type_name = str(work_type.get("name", "")).strip()
        operation = str(work_type.get("operation", "")).strip().lstrip("/")
        if not operation:
            continue

        api_url = f"{base_url}/{operation}"
        source_item_count = 0

        # 문서 기준은 ServiceKey지만 일부 공공 API 호환성을 위해 serviceKey도 보조 시도합니다.
        key_names = ["ServiceKey", "serviceKey"]
        work_type_success = False

        for service_key_name in key_names:
            work_type_items_collected = 0
            try:
                for page_no in range(1, max_pages + 1):
                    params = _build_params(config, page_no=page_no, service_key_name=service_key_name)

                    print(
                        f"[정보] 나라장터 사전규격 {work_type_name} 목록 조회 시작: "
                        f"operation={operation}, pageNo={page_no}, numOfRows={params['numOfRows']}"
                    )

                    response = request_with_retry(api_url, params=params, max_retries=3)
                    if debug_response and page_no == 1:
                        safe_url = response.url.replace(str(params.get(service_key_name, "")), "***")
                        print(f"[디버그] 요청 URL: {safe_url}")
                        print(f"[디버그] 응답 앞부분: {response.text[:1200]}")

                    items = _extract_items_from_response(response)
                    source_item_count += len(items)
                    work_type_items_collected += len(items)

                    print(
                        f"[정보] 나라장터 사전규격 {work_type_name} "
                        f"pageNo={page_no} 원본 item 수: {len(items)}"
                    )

                    if debug_response and items:
                        print(f"[디버그] 첫 번째 item 키: {list(items[0].keys())}")
                        guessed_title = _guess_prespec_title(items[0], keywords)
                        print(f"[디버그] 첫 번째 item 제목 후보: {guessed_title}")

                    if not items:
                        print("[정보] 응답 item이 없어 해당 업무구분의 다음 페이지 조회를 중단합니다.")
                        break

                    page_count = 0
                    keyword_match_count = 0
                    expired_opinion_count = 0
                    convert_excluded_count = 0
                    active_only = bool(gcfg.get("active_only", True))

                    for item in items:
                        title = _guess_prespec_title(item, keywords)
                        matched_keyword = _find_matched_keyword_in_title(title, keywords)
                        if keywords and not matched_keyword:
                            continue

                        keyword_match_count += 1

                        opinion_close_date = _normalize_date(
                            _first_value(
                                item,
                                [
                                    "opninRgstClseDt",
                                    "opinionCloseDate",
                                    "opinionRgstClseDt",
                                    "opninClseDt",
                                    "의견등록마감일시",
                                ],
                            )
                        )

                        if active_only and not _is_active_prespec(opinion_close_date):
                            expired_opinion_count += 1
                            if debug_response:
                                print(
                                    f"[디버그] 의견마감 경과 제외: title={title}, "
                                    f"opninRgstClseDt={opinion_close_date or '-'}"
                                )
                            continue

                        prespec = _convert_item_to_prespec(item, work_type_name, matched_keyword, config)
                        if prespec is not None:
                            all_prespecs.append(prespec)
                            page_count += 1
                        else:
                            convert_excluded_count += 1

                    print(
                        f"[정보] 나라장터 사전규격 {work_type_name} "
                        f"pageNo={page_no} 키워드 일치 수: {keyword_match_count}, "
                        f"의견마감 경과 제외 수: {expired_opinion_count}, "
                        f"최종 수집 수: {page_count}, 기타 제외 수: {convert_excluded_count}"
                    )

                    if len(items) < int(params["numOfRows"]):
                        print("[정보] 마지막 페이지로 판단되어 해당 업무구분의 조회를 중단합니다.")
                        break

                work_type_success = True
                break
            except Exception as exc:
                print(f"[경고] 나라장터 사전규격 {work_type_name} 조회 실패: key={service_key_name} - {exc}")
                continue

        if not work_type_success:
            print(f"[경고] 나라장터 사전규격 {work_type_name} 업무구분 조회를 모두 실패했습니다.")
        elif source_item_count == 0:
            print(f"[경고] 나라장터 사전규격 {work_type_name} 업무구분에서 원본 item이 0건입니다.")

    deduped: dict[str, PrespecNotice] = {}
    for prespec in all_prespecs:
        deduped[prespec.notice_id] = prespec

    prespecs = sorted(
        deduped.values(),
        key=lambda n: (n.opinion_close_date or "9999-12-31", n.registered_date or "", n.title),
    )

    print(f"[정보] 나라장터 사전규격 최종 수집 수: {len(prespecs)}")
    return prespecs