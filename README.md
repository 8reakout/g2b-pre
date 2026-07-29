# 나라장터 사전규격 이메일 모니터링

`g2b_email_scheduler`를 기반으로 만든 사전규격정보서비스용 프로그램입니다.
나라장터 사전규격정보서비스 API에서 최근 사전규격 목록을 가져온 뒤, `config.yaml`의 키워드가 품명/사업명에 포함된 항목만 HTML 이메일로 발송합니다.

## 주요 기능

- 조달청 나라장터 사전규격정보서비스 조회
- 용역 / 물품 / 공사 / 외자 업무구분별 조회
- 품명/사업명 키워드 포함 필터
- 의견등록마감일시가 지난 항목 제외
- 신규 / 기존 항목 구분을 위한 `seen_prespec_ids.json` 관리
- `g2b_prespec_latest.html` 생성 및 이메일 첨부
- GitHub Actions 실행 지원

## 설치

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## 설정

`config.example.yaml`을 복사해 `config.yaml`을 만들고 키워드를 수정합니다.

```yaml
g2b_prespec:
  keywords:
    - "대학교"
    - "AI"
    - "LMS"
```

키워드는 `*교육*`처럼 쓰지 않고 `"교육"`이라고만 넣으면 됩니다.

## 인증키 및 메일 설정

`.env.example`을 복사해 `.env`를 만들고 값을 입력합니다.

```env
G2B_PRESPEC_SERVICE_KEY=공공데이터포털_인증키
SMTP_USER=보내는메일@gmail.com
SMTP_PASSWORD=구글_앱비밀번호
MAIL_FROM=보내는메일@gmail.com
MAIL_TO=받는메일@example.com
```

기존 나라장터 입찰공고 프로그램과 같은 인증키를 사용하는 경우 `G2B_SERVICE_KEY`도 지원합니다.

## 실행

```powershell
python main.py
```

실행 결과로 `g2b_prespec_latest.html`, `latest_prespecs.json`, `seen_prespec_ids.json`이 생성/갱신됩니다.

## GitHub Actions Secrets

- `G2B_PRESPEC_SERVICE_KEY` 또는 `G2B_SERVICE_KEY`
- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_USE_TLS`
- `SMTP_USER`
- `SMTP_PASSWORD`
- `MAIL_FROM`
- `MAIL_TO`
- `MAIL_CC`
