# Self Security Scanner

> 콘솔에서 **명령어 한 줄**이면 크롤링부터 SQLi·XSS·서버 노출까지
> 모든 취약점을 한 번에 검사하고, 발견된 모든 것을 **로그로** 띄웁니다.
> One console command runs every check and logs every finding in one stream.

> ⚠️ **인가된 대상만 검사하세요 (AUTHORIZED USE ONLY).**
> 본인이 소유했거나 서면 허가를 받은 시스템만 검사하세요. 이 도구는 실제
> 공격 트래픽(SQLi/XSS/명령어 주입 등)을 대상에 전송합니다. 허가 없는
> 스캔은 불법일 수 있습니다.

---

## 빠른 시작 (Quick start)

```bash
cd security_scanner

# (선택) 의존성 설치 — 없어도 urllib 폴백으로 동작합니다
pip install -r requirements.txt

# 내 사이트 검사 — 이 한 줄이면 끝
python3 scan.py https://your-site.example.com
```

실행하면:

1. 대상을 크롤링해서 **공격 표면(URL·파라미터·폼)** 을 수집하고
2. 아래 **모든 모듈**을 실행하며
3. 발견되는 취약점을 **실시간 로그로 출력**하고
4. `scan_reports/` 에 전체 로그(`.log`)와 JSON 리포트(`.json`)를 저장합니다.

권한 확인 프롬프트를 건너뛰려면 `--yes` 를 붙이세요.

```bash
python3 scan.py https://your-site.example.com --yes
```

---

## 무엇을 검사하나 (Coverage)

“서버 권한이든 SQLi든 뭐든 별도라는 게 없게” — 하나의 스캔으로 아래를 전부 점검합니다.

| 모듈 | 검사 내용 |
|------|-----------|
| `recon` | 서버/프레임워크 핑거프린트, 버전 배너 노출, robots.txt·sitemap·security.txt |
| `headers` | 누락/취약한 보안 헤더(CSP, HSTS, X-Frame-Options 등), 안전하지 않은 쿠키 플래그 |
| `tls` | 인증서 만료/자체서명/호스트 불일치, 구버전 TLS(1.0/1.1), HTTP→HTTPS 리다이렉트 |
| `ports` | 노출된 TCP 서비스/위험 포트 (DB 3306·5432·27017·6379, RDP 3389, Docker API 2375 등) |
| `http_methods` | 위험한 HTTP 메서드(PUT/DELETE/TRACE/CONNECT) 허용 여부 |
| `cors` | CORS 설정 오류(임의 Origin 반사, `*`+credentials, `null` 신뢰) |
| `sensitive_files` | `.git`/`.env`/백업/DB덤프/개인키 노출, 디렉터리 리스팅, 관리자 페이지 |
| `sqli` | **SQL 인젝션** — 에러 기반 / 불리언 블라인드 / 시간 기반 블라인드 |
| `xss` | 반사형 크로스사이트 스크립팅(컨텍스트별 페이로드) |
| `traversal` | 경로 탐색 / 로컬 파일 인클루전(LFI) |
| `cmdi` | OS 명령어 주입 — 출력 기반 / 시간 기반 블라인드 |
| `open_redirect` | 검증되지 않은 오픈 리다이렉트 |
| `csrf` | 반-CSRF 토큰이 없는 상태 변경 폼 |

모든 페이로드는 **비파괴적**입니다(읽기/지연만, 쓰기·삭제 없음).

---

## 자주 쓰는 옵션 (Options)

```bash
# 특정 모듈만 실행
python3 scan.py https://site.com --only sqli,xss,headers

# 특정 모듈 제외 / 포트 스캔 생략
python3 scan.py https://site.com --skip ports
python3 scan.py https://site.com --no-ports

# 더 공격적으로 (모든 파라미터에 traversal/redirect 테스트)
python3 scan.py https://site.com --aggressive

# 느린 시간 기반 블라인드 테스트 끄기 (더 빠름)
python3 scan.py https://site.com --no-time-based

# 서버 부담을 줄이려면 초당 요청 수 제한
python3 scan.py https://site.com --rate 5

# 로그인 후 영역까지 검사 — 쿠키/헤더 지정
python3 scan.py https://site.com --cookie "session=abc123" --header "X-Api-Key: key"

# Burp/ZAP 프록시로 트래픽 라우팅
python3 scan.py https://site.com --proxy http://127.0.0.1:8080

# 크롤링 범위 조정
python3 scan.py https://site.com --max-pages 100 --max-depth 4

# HIGH 이상만 화면에 출력(파일에는 전부 저장)
python3 scan.py https://site.com --min-severity high
```

전체 옵션: `python3 scan.py --help`

---

## 결과 읽기 (Output)

화면에는 심각도별 색상으로 발견 항목이 실시간 출력되고, 종료 시 요약이 나옵니다:

```
[CRITICAL] SQL Injection: SQL injection (error-based) — MySQL
    url:       https://site.com/item.php
    parameter: id
    evidence:  payload "'" triggered MySQL error: ...
    confidence: confirmed
...
================================================================
 SCAN COMPLETE  ->  https://site.com
 duration: 42.3s    findings: 17
================================================================
  [!] CRITICAL  2
  [!] HIGH      3
  [ ] MEDIUM    5
  [ ] LOW       4
  [ ] INFO      3
================================================================
 full log:    scan_reports/scan_site.com_20260914_....log
 json report: scan_reports/scan_site.com_20260914_....json
================================================================
```

- **CRITICAL/HIGH** — 즉시 조치 필요 (RCE, SQLi, 자격증명 노출 등)
- **MEDIUM** — 강력 권장 (헤더, 오픈 리다이렉트, TLS 등)
- **LOW/INFO** — 방어 심화 / 정보성

JSON 리포트는 CI 파이프라인이나 다른 도구에 연동하기 좋습니다.

**종료 코드**: `0` = 발견 없음, `1` = LOW/MEDIUM 존재, `2` = HIGH/CRITICAL 존재
(CI에서 실패 처리하기 좋습니다.)

---

## 스크립트로 사용 (Library use)

```python
from scanner import Scanner, Severity

report = Scanner("https://your-site.com", {
    "yes": True,          # 프롬프트는 CLI 전용; 코드에서는 그냥 실행됩니다
    "time_based": True,
    "rate": 10,
}).run()

print(report["summary"])           # {'CRITICAL': 2, 'HIGH': 3, ...}
for f in report["findings"]:
    print(f["severity"], f["category"], f["title"], f["url"])
```

---

## 동작 방식 (How it works)

```
scan.py                 # CLI 진입점 (인자 파싱 + 권한 확인)
scanner/
├── core.py             # 오케스트레이터: 크롤링 → 전 모듈 실행 → 리포트
├── crawler.py          # 같은 호스트 링크/폼/파라미터 수집
├── http_client.py      # requests 래퍼(타임아웃/재시도/레이트리밋; urllib 폴백)
├── logger.py           # 발견 항목 수집 + 실시간 출력 + 로그/JSON 저장
└── modules/            # 각 취약점 클래스별 점검 모듈
```

새 점검을 추가하려면 `modules/base.py`의 `Module`을 상속한 클래스를 만들고
`modules/__init__.py`의 `ALL_MODULES`에 등록하면 됩니다.

---

## 한계 (Limitations)

- 블랙박스·시그니처 기반 스캐너입니다. 발견되지 않았다고 안전을 보장하지
  않으며(거짓 음성), 일부 결과는 수동 확인이 필요할 수 있습니다(거짓 양성 —
  `confidence` 값 참고).
- 저장형 XSS, 인증/인가 로직, 비즈니스 로직 취약점, SSRF(콜백 서버 필요)
  등은 자동 탐지 범위를 벗어나므로 별도 검토가 필요합니다.
- 심층 점검에는 OWASP ZAP, Nikto, sqlmap, nuclei 등 전문 도구 병행을 권장합니다.
```
