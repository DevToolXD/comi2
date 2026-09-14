# comi2 — 브라우저 콘솔용 웹 취약점 진단 스크립트

내 사이트를 브라우저에서 연 뒤, **콘솔에 한 번 붙여넣기**만 하면 클라이언트 사이드
보안 취약점을 자동으로 점검하고 심각도별 리포트를 출력하는 도구입니다.

> ⚠️ **반드시 본인 소유이거나 명시적으로 진단 권한이 있는 사이트에서만 사용하세요.**
> 이 스크립트는 상태를 변경하는 요청을 보내지 않는 **수동(passive) 진단**만 수행합니다.

---

## 사용법

1. 진단할 사이트를 브라우저에서 엽니다. (로그인이 필요한 페이지는 **로그인한 상태**로 열면 더 정확합니다.)
2. `F12`(또는 우클릭 → 검사)로 개발자 도구를 열고 **Console(콘솔)** 탭으로 이동합니다.
3. 브라우저가 "붙여넣기 위험" 경고를 띄우면, 안내에 따라 `allow pasting` 을 입력해 허용합니다.
4. [`security-scanner.js`](./security-scanner.js) 파일 **전체 내용**을 복사해 콘솔에 붙여넣고 `Enter`.
5. 심각도별 리포트가 출력됩니다. 결과 객체는 `window.__COMI2_SEC` 에 저장되어 다시 조회할 수 있습니다.

점검하려는 페이지를 이동할 때마다(로그인 후 화면, 마이페이지, 결제 화면 등)
다시 붙여넣어 실행하면 화면별로 진단할 수 있습니다.

---

## 무엇을 점검하나요?

| 분류 | 점검 내용 |
|------|-----------|
| HTTPS/전송 | HTTP 사용 여부, 혼합 콘텐츠(Mixed Content) |
| 보안 헤더 | HSTS, X-Content-Type-Options, Referrer-Policy, Permissions-Policy, COOP 등 |
| CSP | Content-Security-Policy 존재/`unsafe-inline`/`unsafe-eval`/와일드카드/누락 지시어 |
| 클릭재킹 | X-Frame-Options / `frame-ancestors` 부재 |
| 쿠키 | JS로 읽히는(HttpOnly 아님) 세션/인증 쿠키 |
| 클라이언트 저장소 | localStorage/sessionStorage 내 토큰·시크릿, JWT 만료 분석 |
| 폼/인증 | 비밀번호 평문(HTTP) 전송, 외부 출처 전송, CSRF 토큰 부재(휴리스틱) |
| XSS 표면 | 인라인 이벤트 핸들러, `javascript:` URI, DOM XSS 위험 싱크(innerHTML/eval/document.write 등) |
| 서드파티/SRI | 외부 스크립트·스타일의 무결성(integrity) 해시 부재 |
| iframe | sandbox 부재, 외부 출처 임베드 |
| 취약 라이브러리 | jQuery / AngularJS / Lodash / Moment / Bootstrap 등 알려진 취약 버전 감지 |
| 시크릿 노출 | 소스 내 AWS/Google/Stripe/GitHub 키, 개인키 블록, 하드코딩된 JWT |
| 정보 노출 | Server/X-Powered-By 헤더, 민감 키워드가 담긴 HTML 주석, 소스맵 참조 |

리포트는 `CRITICAL / HIGH / MEDIUM / LOW / INFO / PASS` 6단계 심각도로 분류되고,
항목별로 **설명·권고 조치·근거**가 함께 표시됩니다. 종합 위험도 점수와 등급(A~F)도 제공합니다.

---

## 한계 (반드시 읽어주세요)

이 도구는 **브라우저 JavaScript로 볼 수 있는 범위**만 점검합니다. 다음은 여기서
확인할 수 없으니 별도 점검이 필요합니다.

- **서버측 취약점**: SQL 인젝션, 인증/인가 우회, 접근제어(IDOR), 서버측 로직 결함, SSRF 등
- 쿠키의 **Secure / SameSite** 실제 적용 여부(→ DevTools → Application → Cookies 에서 확인)
- 번들러로 묶여 전역 노출이 없는 라이브러리 버전(→ `package.json` / `npm audit` 로 교차 확인)
- CSRF 토큰 점검은 휴리스틱이라 **오탐/미탐**이 있을 수 있습니다.

서버측까지 포함한 정식 진단이 필요하면 OWASP ZAP, Burp Suite 등의 전용 도구나
전문 모의해킹 절차를 병행하세요.

---

## 파일

- [`security-scanner.js`](./security-scanner.js) — 콘솔에 붙여넣어 실행하는 진단 스크립트 (의존성 없음)
