/*!
 * comi2 — Client-Side Security Scanner (브라우저 콘솔용 취약점 진단 도구)
 * ---------------------------------------------------------------------------
 * 사용법:
 *   1) 진단할 "본인 사이트"를 브라우저에서 연다. (로그인 상태로 열면 더 정확)
 *   2) F12 → Console 탭을 연다.
 *   3) 이 파일 전체를 복사해서 콘솔에 붙여넣고 Enter.
 *   4) 심각도별 리포트가 출력되고, 결과 객체는 window.__COMI2_SEC 에 저장된다.
 *
 * ⚠️ 반드시 본인 소유이거나 명시적으로 진단 권한이 있는 사이트에서만 실행하세요.
 *    이 도구는 상태를 변경하는 요청을 보내지 않는 "수동(passive)" 진단만 수행합니다.
 *
 * 한계:
 *   - 브라우저 JS 샌드박스 안에서 볼 수 있는 것만 점검합니다.
 *   - Secure / SameSite 쿠키 속성, HttpOnly 여부의 확정, 서버측 로직/SQLi 등은
 *     여기서 완전히 확인할 수 없습니다. DevTools의 Application/Network 탭 및
 *     서버측 점검을 병행하세요.
 */
(function comi2Scanner() {
  'use strict';

  const VERSION = '1.0.0';
  const findings = [];

  // 심각도 가중치 (정렬/집계용)
  const WEIGHT = { CRITICAL: 5, HIGH: 4, MEDIUM: 3, LOW: 2, INFO: 1, PASS: 0 };

  // 콘솔 배지 스타일
  const STYLE = {
    CRITICAL: 'background:#b00020;color:#fff;font-weight:bold;padding:1px 6px;border-radius:3px;',
    HIGH:     'background:#e65100;color:#fff;font-weight:bold;padding:1px 6px;border-radius:3px;',
    MEDIUM:   'background:#f9a825;color:#000;font-weight:bold;padding:1px 6px;border-radius:3px;',
    LOW:      'background:#1565c0;color:#fff;font-weight:bold;padding:1px 6px;border-radius:3px;',
    INFO:     'background:#546e7a;color:#fff;font-weight:bold;padding:1px 6px;border-radius:3px;',
    PASS:     'background:#2e7d32;color:#fff;font-weight:bold;padding:1px 6px;border-radius:3px;',
    TITLE:    'background:#111;color:#00e676;font-weight:bold;padding:3px 8px;border-radius:3px;',
    CAT:      'color:#8ab4f8;font-weight:bold;font-size:12px;',
    DIM:      'color:#9e9e9e;',
  };

  // 발견 항목 추가
  //  sev: 심각도, cat: 분류, title: 요약, detail: 설명,
  //  fix: 권고 조치, evidence: 근거(문자열/배열)
  function add(sev, cat, title, detail, fix, evidence) {
    findings.push({ sev, cat, title, detail, fix, evidence: evidence == null ? '' : evidence });
  }

  // 안전 실행 래퍼 (한 점검이 실패해도 전체가 죽지 않도록)
  function safe(name, fn) {
    try { return fn(); }
    catch (e) { add('INFO', '진단오류', `[${name}] 점검 중 예외 발생`, String(e && e.message || e), '해당 점검은 건너뜁니다.'); }
  }
  async function safeAsync(name, fn) {
    try { return await fn(); }
    catch (e) { add('INFO', '진단오류', `[${name}] 점검 중 예외 발생`, String(e && e.message || e), '해당 점검은 건너뜁니다.'); }
  }

  // ---- 유틸 --------------------------------------------------------------

  const isHTTPS = location.protocol === 'https:';
  const originHost = location.hostname;

  function sameOrigin(url) {
    try { return new URL(url, location.href).origin === location.origin; }
    catch { return true; } // 상대경로 등은 same-origin 취급
  }

  function truncate(s, n = 220) {
    s = String(s);
    return s.length > n ? s.slice(0, n) + '…(생략)' : s;
  }

  // 버전 비교: a<b -> -1, a==b -> 0, a>b -> 1
  function cmpVer(a, b) {
    const pa = String(a).split('.').map(x => parseInt(x, 10) || 0);
    const pb = String(b).split('.').map(x => parseInt(x, 10) || 0);
    const len = Math.max(pa.length, pb.length);
    for (let i = 0; i < len; i++) {
      const d = (pa[i] || 0) - (pb[i] || 0);
      if (d) return d > 0 ? 1 : -1;
    }
    return 0;
  }
  const ltVer = (a, b) => cmpVer(a, b) < 0;

  function b64urlDecode(s) {
    s = String(s).replace(/-/g, '+').replace(/_/g, '/');
    const pad = s.length % 4;
    if (pad) s += '='.repeat(4 - pad);
    return decodeURIComponent(
      atob(s).split('').map(c => '%' + ('00' + c.charCodeAt(0).toString(16)).slice(-2)).join('')
    );
  }

  // JWT처럼 보이는 문자열 여부 + 페이로드 요약
  function inspectJWT(val) {
    const m = /^([A-Za-z0-9_-]+)\.([A-Za-z0-9_-]+)\.([A-Za-z0-9_-]+)$/.exec(String(val).trim());
    if (!m) return null;
    try {
      const payload = JSON.parse(b64urlDecode(m[2]));
      const info = {};
      if (payload.exp) info.exp = new Date(payload.exp * 1000).toISOString();
      if (payload.iat) info.iat = new Date(payload.iat * 1000).toISOString();
      if (payload.sub) info.sub = payload.sub;
      if (payload.role || payload.roles) info.role = payload.role || payload.roles;
      info.expired = payload.exp ? (Date.now() > payload.exp * 1000) : undefined;
      return info;
    } catch { return {}; }
  }

  // ---- 점검 1) 전송 계층 / HTTPS / 혼합 콘텐츠 ---------------------------

  function checkTransport() {
    if (!isHTTPS) {
      if (location.protocol === 'http:') {
        add('HIGH', 'HTTPS/전송', '사이트가 HTTPS가 아닌 HTTP로 제공됨',
          '평문(HTTP) 통신은 중간자 공격(MITM)에 노출되어 세션/입력값이 탈취될 수 있습니다.',
          'TLS 인증서를 적용하고 모든 HTTP 요청을 HTTPS로 301 리다이렉트하세요. HSTS도 함께 적용.',
          location.origin);
      }
      return; // 혼합 콘텐츠는 HTTPS 페이지에서만 의미 있음
    }

    // 혼합 콘텐츠: DOM 상의 http:// 리소스
    const mixed = [];
    document.querySelectorAll('[src],[href],[data-src],form[action],[srcset]').forEach(el => {
      ['src', 'href', 'action', 'data-src'].forEach(attr => {
        const v = el.getAttribute && el.getAttribute(attr);
        if (v && /^http:\/\//i.test(v)) mixed.push(`<${el.tagName.toLowerCase()} ${attr}> ${v}`);
      });
    });
    // 실제로 로드된 http 리소스 (Performance API)
    try {
      performance.getEntriesByType('resource').forEach(r => {
        if (/^http:\/\//i.test(r.name)) mixed.push(`loaded: ${r.name}`);
      });
    } catch {}

    if (mixed.length) {
      const active = mixed.filter(x => /<script|\.js(\?|$)|action>/i.test(x));
      add(active.length ? 'HIGH' : 'MEDIUM', 'HTTPS/전송',
        `혼합 콘텐츠(Mixed Content) ${mixed.length}건 발견`,
        'HTTPS 페이지에서 HTTP 리소스를 불러오면 해당 리소스가 변조/탈취될 수 있고, 능동적(script) 혼합 콘텐츠는 페이지 전체를 장악당할 수 있습니다.',
        '모든 리소스 URL을 https:// 로 변경하고, CSP에 upgrade-insecure-requests 를 추가하세요.',
        [...new Set(mixed)].slice(0, 15));
    } else {
      add('PASS', 'HTTPS/전송', 'HTTPS 사용 + 혼합 콘텐츠 미검출', 'DOM/로드 리소스에서 http:// 참조가 발견되지 않았습니다.', '');
    }
  }

  // ---- 점검 2) 보안 응답 헤더 / CSP -------------------------------------

  async function checkHeaders() {
    let res;
    try {
      res = await fetch(location.href, { method: 'GET', credentials: 'include', cache: 'no-store' });
    } catch (e) {
      add('INFO', '보안헤더', '응답 헤더를 가져오지 못함',
        `현재 URL을 fetch 하는 데 실패했습니다: ${e.message}`,
        'DevTools → Network 탭에서 문서 응답의 헤더를 수동으로 확인하세요.');
      return;
    }
    const h = res.headers;
    const get = n => h.get(n);

    // --- CSP ---
    const cspHeader = get('content-security-policy');
    const cspMeta = (document.querySelector('meta[http-equiv="Content-Security-Policy" i]') || {})
      .getAttribute?.('content') || null;
    analyzeCSP(cspHeader, cspMeta);

    // --- 클릭재킹 (X-Frame-Options / frame-ancestors) ---
    const xfo = get('x-frame-options');
    const csp = cspHeader || cspMeta || '';
    const hasFA = /frame-ancestors/i.test(csp);
    if (!xfo && !hasFA) {
      add('HIGH', '보안헤더', '클릭재킹 방어 부재 (X-Frame-Options / frame-ancestors 없음)',
        '외부 사이트가 이 페이지를 <iframe>으로 감싸 사용자를 속이는 클릭재킹 공격이 가능합니다.',
        "응답 헤더에 'X-Frame-Options: DENY'(또는 SAMEORIGIN)를 추가하거나, CSP에 frame-ancestors 'none'/'self' 를 설정하세요.");
    } else {
      add('PASS', '보안헤더', '클릭재킹 방어 설정됨', `X-Frame-Options=${xfo || '-'}, frame-ancestors=${hasFA ? '설정됨' : '-'}`, '');
    }

    // --- HSTS ---
    if (isHTTPS) {
      const hsts = get('strict-transport-security');
      if (!hsts) {
        add('MEDIUM', '보안헤더', 'HSTS(Strict-Transport-Security) 미설정',
          'HSTS가 없으면 최초 접속 시 HTTP 다운그레이드/스트립 공격에 노출될 수 있습니다.',
          "'Strict-Transport-Security: max-age=31536000; includeSubDomains; preload' 추가를 권장합니다.");
      } else {
        const mAge = /max-age=(\d+)/i.exec(hsts);
        const secs = mAge ? parseInt(mAge[1], 10) : 0;
        if (secs < 15552000) {
          add('LOW', '보안헤더', `HSTS max-age가 짧음 (${secs}s)`,
            'max-age가 너무 짧으면 보호 효과가 제한적입니다.', 'max-age를 최소 6개월(15552000) 이상으로 설정하세요.', hsts);
        } else {
          add('PASS', '보안헤더', 'HSTS 설정됨', hsts, '');
        }
      }
    }

    // --- X-Content-Type-Options ---
    const xcto = get('x-content-type-options');
    if (!xcto || !/nosniff/i.test(xcto)) {
      add('LOW', '보안헤더', 'X-Content-Type-Options: nosniff 미설정',
        'MIME 스니핑으로 인해 파일이 의도치 않은 타입으로 실행될 수 있습니다.',
        "'X-Content-Type-Options: nosniff' 헤더를 추가하세요.");
    } else {
      add('PASS', '보안헤더', 'X-Content-Type-Options: nosniff 설정됨', '', '');
    }

    // --- Referrer-Policy ---
    if (!get('referrer-policy') && !document.querySelector('meta[name="referrer" i]')) {
      add('LOW', '보안헤더', 'Referrer-Policy 미설정',
        '외부로 이동 시 전체 URL(민감한 쿼리 파라미터 포함)이 Referer로 유출될 수 있습니다.',
        "'Referrer-Policy: strict-origin-when-cross-origin' 등을 설정하세요.");
    }

    // --- Permissions-Policy ---
    if (!get('permissions-policy') && !get('feature-policy')) {
      add('INFO', '보안헤더', 'Permissions-Policy 미설정',
        '카메라/마이크/위치 등 강력한 기능 접근을 제한하지 않고 있습니다.',
        '사용하지 않는 기능은 Permissions-Policy로 비활성화하세요. 예: geolocation=(), camera=()');
    }

    // --- 정보 노출 헤더 ---
    ['server', 'x-powered-by', 'x-aspnet-version', 'x-aspnetmvc-version'].forEach(n => {
      const v = get(n);
      if (v) {
        add('LOW', '정보노출', `서버 정보 노출 헤더: ${n}`,
          `응답 헤더가 서버/프레임워크 버전을 드러냅니다(${n}: ${v}). 공격자의 정찰을 돕습니다.`,
          `${n} 헤더를 제거하거나 값을 감추세요.`, `${n}: ${v}`);
      }
    });

    // --- CORS 과다 허용 ---
    const acao = get('access-control-allow-origin');
    const acac = get('access-control-allow-credentials');
    if (acao === '*' && /true/i.test(acac || '')) {
      add('HIGH', 'CORS', 'CORS 설정 위험: ACAO:* + Allow-Credentials:true',
        '와일드카드 Origin과 자격증명 허용을 동시에 쓰면 사양상 무효이거나, 잘못 구현 시 임의 출처가 인증된 응답을 읽을 수 있습니다.',
        '자격증명을 쓰는 경우 허용 Origin을 명시적 화이트리스트로 제한하세요.', `ACAO=${acao}, ACAC=${acac}`);
    } else if (acao === '*') {
      add('INFO', 'CORS', 'Access-Control-Allow-Origin: * (와일드카드)',
        '문서 응답에 와일드카드 CORS가 설정되어 있습니다. 민감 API에는 부적절할 수 있습니다.',
        '민감한 데이터를 반환하는 엔드포인트는 Origin을 제한하세요.', `ACAO=${acao}`);
    }

    // --- COOP/COEP/CORP (교차 출처 격리) ---
    if (!get('cross-origin-opener-policy')) {
      add('INFO', '보안헤더', 'Cross-Origin-Opener-Policy 미설정',
        'COOP가 없으면 팝업/opener를 통한 교차 출처 상호작용 위험이 있습니다.',
        "'Cross-Origin-Opener-Policy: same-origin' 적용을 검토하세요.");
    }
  }

  // CSP 세부 분석
  function analyzeCSP(headerVal, metaVal) {
    const val = headerVal || metaVal;
    if (!val) {
      add('HIGH', 'CSP', 'Content-Security-Policy 미설정',
        'CSP가 없으면 XSS 발생 시 이를 완화할 2차 방어선이 없습니다.',
        "최소한 default-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none' 부터 시작해 점진 적용하세요.");
      return;
    }
    const csp = val.toLowerCase();
    if (metaVal && !headerVal) {
      add('LOW', 'CSP', 'CSP가 meta 태그로만 설정됨',
        'meta CSP는 frame-ancestors 등 일부 지시어가 무시되고, 문서 파싱 전 리소스에는 적용되지 않습니다.',
        'CSP는 HTTP 응답 헤더로 전달하는 것을 권장합니다.');
    }
    const scriptCtx = /script-src[^;]*/.exec(csp)?.[0] || /default-src[^;]*/.exec(csp)?.[0] || '';
    if (/'unsafe-inline'/.test(scriptCtx)) {
      add('HIGH', 'CSP', "CSP에 script 'unsafe-inline' 허용",
        "'unsafe-inline'은 인라인 스크립트/이벤트 핸들러 실행을 허용하여 XSS 방어를 사실상 무력화합니다.",
        "nonce 또는 해시 기반 CSP로 전환하고 'unsafe-inline'을 제거하세요.", truncate(scriptCtx));
    }
    if (/'unsafe-eval'/.test(scriptCtx)) {
      add('MEDIUM', 'CSP', "CSP에 'unsafe-eval' 허용",
        "eval/Function 등을 허용해 DOM 기반 XSS 표면을 넓힙니다.",
        "'unsafe-eval'을 제거하고 eval 사용 코드를 리팩터링하세요.", truncate(scriptCtx));
    }
    if (/(^|\s)script-src[^;]*(\s|:)\*/.test(csp) || (/default-src[^;]*\*/.test(csp) && !/script-src/.test(csp))) {
      add('HIGH', 'CSP', 'CSP script-src에 와일드카드(*) 허용',
        '임의 출처의 스크립트 로드를 허용하여 CSP가 무의미해집니다.',
        '스크립트 출처를 명시적 도메인 화이트리스트로 제한하세요.', truncate(scriptCtx));
    }
    if (!/object-src/.test(csp) && !/default-src[^;]*'none'/.test(csp)) {
      add('LOW', 'CSP', "CSP object-src 'none' 미설정",
        '플러그인(object/embed) 기반 공격 벡터가 차단되지 않습니다.', "object-src 'none' 을 추가하세요.");
    }
    if (!/base-uri/.test(csp)) {
      add('LOW', 'CSP', 'CSP base-uri 미설정',
        '<base> 태그 주입으로 상대경로 리소스가 공격자 서버로 유도될 수 있습니다.', "base-uri 'self' 를 추가하세요.");
    }
    add('INFO', 'CSP', 'CSP 원문(참고용)', '현재 적용된 CSP 정책 전문입니다. 지시어를 직접 검토하세요.', '', truncate(val, 500));
  }

  // ---- 점검 3) 쿠키 -----------------------------------------------------

  function checkCookies() {
    const raw = document.cookie;
    if (!raw) {
      add('PASS', '쿠키', 'JS에서 읽히는 쿠키 없음',
        'document.cookie가 비어 있습니다(세션 쿠키가 HttpOnly이거나 쿠키 미사용). 좋은 신호입니다.', '');
      return;
    }
    const names = raw.split(';').map(c => c.split('=')[0].trim()).filter(Boolean);
    const sessionLike = /sess|sid|token|jwt|auth|login|remember|PHPSESSID|JSESSIONID|connect\.sid|_csrf|xsrf/i;
    const risky = names.filter(n => sessionLike.test(n) && !/^(_csrf|csrf|xsrf)/i.test(n));

    add(risky.length ? 'HIGH' : 'MEDIUM', '쿠키',
      `JavaScript로 읽히는 쿠키 ${names.length}개 (HttpOnly 아님)`,
      'document.cookie로 접근 가능한 쿠키는 HttpOnly가 아니므로, XSS가 발생하면 그대로 탈취됩니다.' +
        (risky.length ? ` 세션/인증으로 의심되는 쿠키: ${risky.join(', ')}` : ''),
      '세션/인증 쿠키에는 HttpOnly, Secure, SameSite=Lax(또는 Strict) 속성을 반드시 설정하세요. ' +
        'Secure/SameSite 실제 적용 여부는 DevTools → Application → Cookies 에서 확인하세요.',
      names.join(', '));
  }

  // ---- 점검 4) localStorage / sessionStorage ----------------------------

  function checkStorage() {
    const sensitive = /token|jwt|secret|password|passwd|auth|apikey|api[_-]?key|session|credential|private|access[_-]?token|refresh[_-]?token/i;
    [['localStorage', localStorage], ['sessionStorage', sessionStorage]].forEach(([label, store]) => {
      let hits = [];
      try {
        for (let i = 0; i < store.length; i++) {
          const k = store.key(i);
          const v = store.getItem(k);
          const jwt = inspectJWT(v);
          if (sensitive.test(k) || jwt) {
            hits.push({ key: k, jwt: jwt || undefined, sample: truncate(v, 60) });
          }
        }
      } catch (e) { return; }
      if (hits.length) {
        const hasToken = hits.some(x => x.jwt || /token|jwt|secret|password|credential/i.test(x.key));
        add(hasToken ? 'HIGH' : 'MEDIUM', '클라이언트저장소',
          `${label}에 민감 정보로 의심되는 값 ${hits.length}건`,
          `${label}는 JS로 완전히 접근 가능하므로 XSS에 그대로 노출됩니다. 인증 토큰/시크릿 저장 장소로 부적절합니다.` +
            (hits.some(x => x.jwt && x.jwt.expired === false) ? ' (유효한 JWT 포함)' : ''),
          '인증 토큰은 HttpOnly+Secure 쿠키에 보관하세요. 부득이 저장 시 민감도 최소화 및 만료를 짧게 하세요.',
          hits.map(x => x.key + (x.jwt ? ` (JWT exp=${x.jwt.exp || '?'}, expired=${x.jwt.expired})` : '')));
      }
    });
  }

  // ---- 점검 5) 폼 / CSRF / 비밀번호 -------------------------------------

  function checkForms() {
    const forms = [...document.forms];
    if (!forms.length) return;
    forms.forEach((f, idx) => {
      const action = f.getAttribute('action') || location.href;
      const method = (f.getAttribute('method') || 'get').toLowerCase();
      const hasPassword = !!f.querySelector('input[type="password" i]');
      const label = f.id || f.name || f.getAttribute('action') || `form#${idx}`;

      // 비밀번호 폼이 HTTP로 전송
      if (hasPassword) {
        if (/^http:\/\//i.test(action) || (!isHTTPS && !/^https:\/\//i.test(action))) {
          add('CRITICAL', '폼/인증', `비밀번호 폼이 평문(HTTP)으로 전송됨 [${label}]`,
            '로그인 자격증명이 암호화되지 않은 채 전송되어 네트워크 상에서 탈취될 수 있습니다.',
            '폼 action과 페이지 모두 HTTPS로 전환하세요.', `action=${action}`);
        } else if (/^https?:\/\//i.test(action) && !sameOrigin(action)) {
          add('HIGH', '폼/인증', `비밀번호 폼이 외부 출처로 전송됨 [${label}]`,
            '자격증명이 다른 도메인으로 전송됩니다. 의도한 것인지 확인이 필요합니다.',
            'action 대상 출처가 신뢰 가능한 곳인지 확인하세요.', `action=${action}`);
        }
      }

      // CSRF 토큰 휴리스틱 (상태변경 POST인데 토큰형 hidden 필드가 없음)
      if (method === 'post') {
        const hidden = [...f.querySelectorAll('input[type="hidden" i]')].map(i => (i.name || '') + ' ' + (i.id || ''));
        const hasCsrf = hidden.some(n => /csrf|xsrf|token|authenticity|nonce|_token/i.test(n));
        if (!hasCsrf) {
          add('LOW', '폼/CSRF', `POST 폼에 CSRF 토큰이 보이지 않음 [${label}]`,
            'CSRF 토큰으로 추정되는 hidden 필드가 없습니다. (프레임워크가 헤더/쿠키로 처리할 수도 있어 오탐 가능)',
            'SameSite 쿠키 + 폼별 CSRF 토큰(동기화 토큰 패턴)을 적용하세요.', `action=${action}, method=POST`);
        }
      }
    });
  }

  // ---- 점검 6) 링크 / 탭내빙(target=_blank) -----------------------------

  function checkLinks() {
    const bad = [...document.querySelectorAll('a[target="_blank" i]')].filter(a => {
      const rel = (a.getAttribute('rel') || '').toLowerCase();
      return !/noopener/.test(rel) && !sameOrigin(a.href);
    });
    if (bad.length) {
      add('LOW', '링크', `rel="noopener" 없는 외부 새창 링크 ${bad.length}건 (탭내빙)`,
        'target="_blank" 외부 링크에 noopener가 없으면 window.opener를 통해 원본 탭이 조작(피싱 리디렉션)될 수 있습니다. (최신 브라우저는 기본 완화)',
        '해당 링크에 rel="noopener noreferrer" 를 추가하세요.',
        bad.slice(0, 10).map(a => a.href));
    }
  }

  // ---- 점검 7) 인라인 이벤트 핸들러 / XSS 표면 --------------------------

  function checkInlineHandlers() {
    const els = document.querySelectorAll('*');
    const handlers = [];
    const jsHrefs = [];
    els.forEach(el => {
      for (const attr of el.attributes || []) {
        if (/^on/i.test(attr.name)) handlers.push(`<${el.tagName.toLowerCase()} ${attr.name}>`);
        if ((attr.name === 'href' || attr.name === 'src') && /^javascript:/i.test(attr.value)) {
          jsHrefs.push(`<${el.tagName.toLowerCase()} ${attr.name}=${truncate(attr.value, 40)}>`);
        }
      }
    });
    if (handlers.length) {
      add('MEDIUM', 'XSS표면', `인라인 이벤트 핸들러 ${handlers.length}건 (onclick 등)`,
        '인라인 핸들러가 많으면 XSS 표면이 넓고, 엄격한 CSP(unsafe-inline 제거) 적용이 어렵습니다.',
        '이벤트 핸들러를 addEventListener 기반 외부 스크립트로 이전하세요.',
        [...new Set(handlers)].slice(0, 12));
    }
    if (jsHrefs.length) {
      add('MEDIUM', 'XSS표면', `javascript: URI 사용 ${jsHrefs.length}건`,
        'javascript: 스킴은 XSS/우회 통로가 될 수 있습니다.',
        'javascript: URI를 제거하고 정상 이벤트 바인딩으로 교체하세요.', jsHrefs.slice(0, 10));
    }

    // 인라인 스크립트 내 위험 싱크 탐지
    const inline = [...document.querySelectorAll('script:not([src])')].map(s => s.textContent || '').join('\n');
    const sinks = [];
    [
      [/\.innerHTML\s*=/g, 'innerHTML 할당'],
      [/\.outerHTML\s*=/g, 'outerHTML 할당'],
      [/document\.write\s*\(/g, 'document.write()'],
      [/\beval\s*\(/g, 'eval()'],
      [/new\s+Function\s*\(/g, 'new Function()'],
      [/\.insertAdjacentHTML\s*\(/g, 'insertAdjacentHTML()'],
      [/(location|location\.href|location\.search|location\.hash|document\.referrer)/g, 'URL/referrer 입력 사용'],
    ].forEach(([re, name]) => {
      const c = (inline.match(re) || []).length;
      if (c) sinks.push(`${name} ×${c}`);
    });
    if (sinks.length) {
      add('LOW', 'XSS표면', '인라인 스크립트에서 DOM XSS 위험 패턴 발견',
        '아래 싱크에 사용자 입력(URL/hash/referrer 등)이 검증 없이 흘러들면 DOM 기반 XSS가 발생할 수 있습니다.',
        '입력을 신뢰하지 말고 textContent/DOM API 또는 안전한 sanitizer(DOMPurify)를 사용하세요.',
        sinks);
    }
  }

  // ---- 점검 8) 서드파티 스크립트 / SRI ---------------------------------

  function checkThirdParty() {
    const scripts = [...document.querySelectorAll('script[src]')];
    const extNoSri = scripts.filter(s => !sameOrigin(s.src) && !s.integrity);
    const httpScripts = scripts.filter(s => /^http:\/\//i.test(s.src));
    if (extNoSri.length) {
      add('MEDIUM', '서드파티/SRI', `SRI(integrity) 없는 외부 스크립트 ${extNoSri.length}건`,
        '외부(CDN 등) 스크립트에 무결성 해시가 없으면, 공급망이 변조될 경우 악성 코드가 그대로 실행됩니다.',
        '외부 스크립트에 integrity + crossorigin 속성(SRI)을 추가하세요.',
        extNoSri.slice(0, 12).map(s => s.src));
    }
    if (httpScripts.length) {
      add('HIGH', '서드파티/SRI', `HTTP로 로드되는 스크립트 ${httpScripts.length}건`,
        '평문으로 로드된 스크립트는 중간자 공격으로 임의 변조가 가능합니다(능동적 혼합 콘텐츠).',
        '모든 스크립트를 HTTPS로 로드하세요.', httpScripts.slice(0, 12).map(s => s.src));
    }
    const links = [...document.querySelectorAll('link[rel="stylesheet"]')];
    const extCssNoSri = links.filter(l => !sameOrigin(l.href) && !l.integrity);
    if (extCssNoSri.length) {
      add('LOW', '서드파티/SRI', `SRI 없는 외부 스타일시트 ${extCssNoSri.length}건`,
        'CSS도 데이터 유출/UI 변조에 악용될 수 있습니다.', '외부 스타일시트에도 SRI 적용을 검토하세요.',
        extCssNoSri.slice(0, 10).map(l => l.href));
    }
  }

  // ---- 점검 9) iframe --------------------------------------------------

  function checkIframes() {
    const frames = [...document.querySelectorAll('iframe')];
    const noSandbox = frames.filter(f => !f.hasAttribute('sandbox'));
    const thirdParty = frames.filter(f => f.src && !sameOrigin(f.src));
    if (noSandbox.length) {
      add('LOW', 'iframe', `sandbox 속성 없는 iframe ${noSandbox.length}건`,
        'sandbox 없이 삽입된 iframe(특히 외부 콘텐츠)은 권한이 과도할 수 있습니다.',
        '필요 최소 권한만 부여하도록 sandbox 속성을 지정하세요.',
        noSandbox.slice(0, 8).map(f => f.src || '(inline)'));
    }
    if (thirdParty.length) {
      add('INFO', 'iframe', `외부 출처 iframe ${thirdParty.length}건`,
        '외부 콘텐츠를 임베드하고 있습니다. 신뢰성/권한을 검토하세요.', '',
        thirdParty.slice(0, 8).map(f => f.src));
    }
  }

  // ---- 점검 10) 취약/오래된 JS 라이브러리 -------------------------------

  function checkLibraries() {
    const detected = [];

    // jQuery
    safe('jQuery', () => {
      const jq = window.jQuery || window.$;
      const v = jq && jq.fn && jq.fn.jquery;
      if (v) {
        detected.push(`jQuery ${v}`);
        if (ltVer(v, '3.5.0')) {
          add('MEDIUM', '취약라이브러리', `오래된 jQuery ${v} (< 3.5.0)`,
            'jQuery < 3.5.0 은 htmlPrefilter XSS(CVE-2020-11022/11023) 등 알려진 취약점이 있습니다.',
            'jQuery 3.5.0 이상으로 업그레이드하세요.', `jquery=${v}`);
        }
      }
    });
    // jQuery Migrate / UI 존재
    safe('jQueryUI', () => {
      const v = window.jQuery && window.jQuery.ui && window.jQuery.ui.version;
      if (v) { detected.push(`jQuery UI ${v}`); if (ltVer(v, '1.13.2')) add('LOW', '취약라이브러리', `오래된 jQuery UI ${v}`, 'jQuery UI 구버전은 XSS 취약점 이력이 있습니다.', 'jQuery UI 1.13.2 이상으로 업그레이드하세요.', v); }
    });
    // AngularJS 1.x (EOL)
    safe('AngularJS', () => {
      const v = window.angular && window.angular.version && window.angular.version.full;
      if (v) {
        detected.push(`AngularJS ${v}`);
        add('MEDIUM', '취약라이브러리', `AngularJS ${v} 사용 (EOL/지원 종료)`,
          'AngularJS(1.x)는 공식 지원이 종료되어 보안 패치가 제공되지 않습니다. 다수의 sandbox 우회/XSS 이력이 있습니다.',
          'Angular(2+) 또는 다른 최신 프레임워크로 마이그레이션하세요.', v);
      }
    });
    // React
    safe('React', () => {
      const v = window.React && window.React.version;
      if (v) { detected.push(`React ${v}`); if (ltVer(v, '16.4.2')) add('LOW', '취약라이브러리', `오래된 React ${v}`, '구버전 React는 알려진 XSS 이슈가 있을 수 있습니다.', '최신 안정 버전으로 업데이트하세요.', v); }
    });
    // Vue
    safe('Vue', () => {
      const v = window.Vue && window.Vue.version;
      if (v) { detected.push(`Vue ${v}`); if (ltVer(v, '2.6.11')) add('LOW', '취약라이브러리', `오래된 Vue ${v}`, '구버전 Vue는 알려진 취약점이 있을 수 있습니다.', '최신 버전으로 업데이트하세요.', v); }
    });
    // Lodash
    safe('Lodash', () => {
      const v = window._ && window._.VERSION;
      if (v) {
        detected.push(`Lodash ${v}`);
        if (ltVer(v, '4.17.21')) add('MEDIUM', '취약라이브러리', `오래된 Lodash ${v} (< 4.17.21)`,
          'Lodash < 4.17.21 은 프로토타입 오염/ReDoS(CVE-2021-23337 등) 취약점이 있습니다.',
          'Lodash 4.17.21 이상으로 업그레이드하세요.', v);
      }
    });
    // Moment.js
    safe('Moment', () => {
      const v = window.moment && window.moment.version;
      if (v) {
        detected.push(`Moment.js ${v}`);
        add('LOW', '취약라이브러리', `Moment.js ${v} 사용 (유지보수 모드)`,
          'Moment.js는 레거시 프로젝트로 전환되었고, 구버전엔 ReDoS(CVE-2022-31129) 이력이 있습니다.',
          '2.29.4+ 로 업데이트하거나 day.js/date-fns/Luxon으로 교체를 검토하세요.', v);
      }
    });
    // Bootstrap (전역 감지 한계 있음)
    safe('Bootstrap', () => {
      const v = (window.bootstrap && window.bootstrap.Tooltip && window.bootstrap.Tooltip.VERSION) ||
                (window.jQuery && window.jQuery.fn && window.jQuery.fn.tooltip && window.jQuery.fn.tooltip.Constructor && window.jQuery.fn.tooltip.Constructor.VERSION);
      if (v) { detected.push(`Bootstrap ${v}`); if (ltVer(v, '4.3.1')) add('LOW', '취약라이브러리', `오래된 Bootstrap ${v}`, 'Bootstrap 구버전(<4.3.1)은 data-* XSS(CVE-2019-8331 등) 이력이 있습니다.', '4.3.1 이상으로 업그레이드하세요.', v); }
    });

    if (detected.length) {
      add('INFO', '취약라이브러리', '감지된 JS 라이브러리 (참고)',
        '전역 객체로 감지된 라이브러리 목록입니다. 번들러로 숨겨진 라이브러리는 감지되지 않을 수 있으니 package.json / npm audit로 교차 확인하세요.',
        '정기적으로 의존성 취약점을 점검하세요(npm audit, Snyk, Dependabot 등).', detected);
    }
  }

  // ---- 점검 11) 노출된 시크릿 / API 키 ---------------------------------

  function checkSecrets() {
    // 스캔 대상: 전체 HTML + 인라인 스크립트
    const html = document.documentElement.outerHTML;
    const inline = [...document.querySelectorAll('script:not([src])')].map(s => s.textContent || '').join('\n');
    const text = html + '\n' + inline;

    const patterns = [
      ['AWS Access Key ID', /\bAKIA[0-9A-Z]{16}\b/g, 'HIGH'],
      ['Google API Key', /\bAIza[0-9A-Za-z\-_]{35}\b/g, 'MEDIUM'],
      ['Stripe Secret Key', /\bsk_live_[0-9a-zA-Z]{16,}\b/g, 'CRITICAL'],
      ['GitHub Token', /\bgh[pousr]_[0-9A-Za-z]{36,}\b/g, 'HIGH'],
      ['Slack Token', /\bxox[baprs]-[0-9A-Za-z-]{10,}\b/g, 'HIGH'],
      ['Private Key Block', /-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----/g, 'CRITICAL'],
      ['Generic Secret 할당', /["']?(?:api[_-]?key|secret|client[_-]?secret|access[_-]?token|passwd|password)["']?\s*[:=]\s*["'][^"'\s]{8,}["']/gi, 'LOW'],
    ];

    patterns.forEach(([name, re, sev]) => {
      const matches = [...new Set((text.match(re) || []))];
      if (matches.length) {
        add(sev, '시크릿노출', `페이지 소스에서 ${name} 패턴 ${matches.length}건 발견`,
          `프론트엔드 소스/HTML에 시크릿으로 보이는 값이 포함되어 있습니다. 클라이언트에 내려온 값은 누구나 볼 수 있습니다.` +
            (name === 'Generic Secret 할당' ? ' (오탐 가능성 있음 — 실제 시크릿인지 확인 필요)' : ''),
          '실제 시크릿이라면 즉시 폐기(rotate)하고, 서버 측에서만 사용하도록 이전하세요. 공개용 키(예: 공개 지도 키)라면 사용 도메인/쿼터를 제한하세요.',
          matches.slice(0, 5).map(m => truncate(m, 40)));
      }
    });

    // JWT가 HTML/스크립트 본문에 직접 박혀있는 경우
    const jwtRe = /\beyJ[A-Za-z0-9_-]{5,}\.eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\b/g;
    const jwts = [...new Set((text.match(jwtRe) || []))];
    if (jwts.length) {
      add('MEDIUM', '시크릿노출', `페이지 소스에 JWT ${jwts.length}건 하드코딩/노출`,
        'JWT가 HTML/스크립트에 직접 포함되어 있습니다. 유효한 인증 토큰이라면 탈취 시 계정 도용이 가능합니다.',
        '토큰을 소스에 직접 넣지 말고, HttpOnly 쿠키 등 안전한 경로로 전달하세요.',
        jwts.slice(0, 3).map(j => {
          const info = inspectJWT(j) || {};
          return truncate(j, 30) + ` (exp=${info.exp || '?'}, expired=${info.expired})`;
        }));
    }

    // 소스맵 노출
    safe('sourcemap', () => {
      const maps = [...document.querySelectorAll('script[src]')]
        .map(s => s.src).filter(Boolean);
      const inlineMap = /\/\/[#@]\s*sourceMappingURL=/.test(inline);
      if (inlineMap) {
        add('INFO', '정보노출', '소스맵(sourceMappingURL) 참조 발견',
          '소스맵이 공개되면 원본 소스 구조가 노출될 수 있습니다.',
          '운영 환경에서는 소스맵을 배포하지 않거나 접근을 제한하세요.');
      }
    });
  }

  // ---- 점검 12) HTML 주석 내 민감 정보 ---------------------------------

  function checkComments() {
    const comments = [];
    const walker = document.createTreeWalker(document.documentElement, NodeFilter.SHOW_COMMENT, null);
    let n, count = 0;
    const kw = /todo|fixme|password|passwd|secret|api[_-]?key|token|internal|debug|hack|bug|admin|backdoor|비밀|암호|내부/i;
    while ((n = walker.nextNode()) && count < 5000) {
      count++;
      const t = n.nodeValue || '';
      if (kw.test(t)) comments.push(truncate(t.trim(), 120));
    }
    if (comments.length) {
      add('LOW', '정보노출', `민감 키워드가 포함된 HTML 주석 ${comments.length}건`,
        'HTML 주석은 사용자에게 그대로 노출됩니다. 내부 정보/자격증명/디버그 힌트가 담기면 위험합니다.',
        '운영 빌드에서 개발용 주석을 제거하세요.', comments.slice(0, 8));
    }
  }

  // ---- 점검 13) 기타 ----------------------------------------------------

  function checkMisc() {
    // autocomplete가 켜진 민감 입력
    safe('autocomplete', () => {
      const pw = [...document.querySelectorAll('input[type="password" i]')]
        .filter(i => (i.getAttribute('autocomplete') || '').toLowerCase() !== 'off' &&
                     (i.getAttribute('autocomplete') || '').toLowerCase() !== 'new-password' &&
                     (i.getAttribute('autocomplete') || '').toLowerCase() !== 'current-password');
      // 참고성 정보 (최신 가이드는 무조건 off 권장은 아님)
      if (pw.length) {
        add('INFO', '폼/인증', `autocomplete 미지정 비밀번호 입력 ${pw.length}건`,
          '공용 PC 환경 등에서 자격증명이 브라우저에 저장될 수 있습니다(맥락에 따라 정상일 수 있음).',
          '민감 입력에는 autocomplete를 명시적으로 설정(new-password/current-password/off)하세요.');
      }
    });

    // document.domain 변경 여부
    safe('domain', () => {
      if (document.domain && document.domain !== location.hostname) {
        add('LOW', '기타', `document.domain 변경됨 (${document.domain})`,
          'document.domain 완화는 동일 상위도메인 내 다른 사이트로부터의 교차 접근을 허용해 격리를 약화시킵니다.',
          'document.domain 사용을 피하고 postMessage 기반 통신으로 대체하세요.', document.domain);
      }
    });

    // opener 존재
    safe('opener', () => {
      if (window.opener) {
        add('INFO', '기타', 'window.opener가 설정되어 있음',
          '다른 페이지에 의해 열렸으며 opener 참조가 존재합니다. 탭내빙 맥락에서 확인하세요.', '');
      }
    });
  }

  // ---- 리포트 출력 ------------------------------------------------------

  function printReport(meta) {
    // 심각도 순 정렬
    findings.sort((a, b) => WEIGHT[b.sev] - WEIGHT[a.sev]);

    const counts = { CRITICAL: 0, HIGH: 0, MEDIUM: 0, LOW: 0, INFO: 0, PASS: 0 };
    findings.forEach(f => { counts[f.sev] = (counts[f.sev] || 0) + 1; });

    console.log('\n%c comi2 Security Scanner %c v' + VERSION + ' ',
      STYLE.TITLE, 'color:#9e9e9e;');
    console.log('%c대상:%c ' + location.origin + '   %c시각:%c ' + meta.when,
      'color:#8ab4f8;font-weight:bold;', 'color:inherit;', 'color:#8ab4f8;font-weight:bold;', 'color:inherit;');

    // 요약 배지
    console.log(
      '%c CRITICAL ' + counts.CRITICAL + ' %c HIGH ' + counts.HIGH + ' %c MEDIUM ' + counts.MEDIUM +
      ' %c LOW ' + counts.LOW + ' %c INFO ' + counts.INFO + ' %c PASS ' + counts.PASS + ' ',
      STYLE.CRITICAL, STYLE.HIGH, STYLE.MEDIUM, STYLE.LOW, STYLE.INFO, STYLE.PASS);

    // 위험 점수(대략): CRITICAL 40, HIGH 20, MEDIUM 8, LOW 3
    const risk = counts.CRITICAL * 40 + counts.HIGH * 20 + counts.MEDIUM * 8 + counts.LOW * 3;
    let grade = 'A';
    if (risk >= 120) grade = 'F';
    else if (risk >= 70) grade = 'D';
    else if (risk >= 40) grade = 'C';
    else if (risk >= 15) grade = 'B';
    console.log('%c종합 위험도 점수: ' + risk + '  (등급 ' + grade + ') %c — 낮을수록 좋음',
      'font-weight:bold;color:' + (risk >= 70 ? '#e53935' : risk >= 15 ? '#fb8c00' : '#43a047') + ';',
      STYLE.DIM);

    // 분류별 그룹 출력
    const cats = [...new Set(findings.map(f => f.cat))];
    cats.forEach(cat => {
      const items = findings.filter(f => f.cat === cat);
      const worst = items.reduce((m, f) => Math.max(m, WEIGHT[f.sev]), 0);
      const collapse = worst <= WEIGHT.LOW; // 경미한 분류는 접어서 표시
      const group = collapse ? console.groupCollapsed : console.group;
      group.call(console, `%c▸ ${cat} %c(${items.length})`, STYLE.CAT, STYLE.DIM);
      items.forEach(f => {
        console.log('%c ' + f.sev + ' %c ' + f.title, STYLE[f.sev], 'font-weight:bold;');
        if (f.detail) console.log('   %c설명:%c ' + f.detail, STYLE.DIM, 'color:inherit;');
        if (f.fix) console.log('   %c조치:%c ' + f.fix, 'color:#66bb6a;', 'color:inherit;');
        if (f.evidence && String(f.evidence).length) {
          if (Array.isArray(f.evidence)) console.log('   %c근거:', STYLE.DIM, f.evidence);
          else console.log('   %c근거:%c ' + truncate(f.evidence, 400), STYLE.DIM, 'color:inherit;');
        }
      });
      console.groupEnd();
    });

    // 표 형태 요약(정렬/필터 편의)
    try {
      console.groupCollapsed('%c▸ 전체 표(테이블) 보기', STYLE.CAT);
      console.table(findings.map(f => ({
        심각도: f.sev, 분류: f.cat, 항목: f.title,
      })));
      console.groupEnd();
    } catch {}

    console.log('%c결과 객체는 window.__COMI2_SEC 에 저장되었습니다. (findings 배열 포함)', STYLE.DIM);
    console.log('%c⚠ 이 진단은 브라우저에서 보이는 범위의 수동 점검입니다. 서버측 취약점(SQLi/권한/로직 등)은 별도 점검이 필요합니다.', STYLE.DIM);
  }

  // ---- 실행 ------------------------------------------------------------

  async function run() {
    console.log('%c[comi2] 취약점 진단 시작…', 'color:#00e676;');
    safe('transport', checkTransport);
    await safeAsync('headers', checkHeaders);
    safe('cookies', checkCookies);
    safe('storage', checkStorage);
    safe('forms', checkForms);
    safe('links', checkLinks);
    safe('inline', checkInlineHandlers);
    safe('thirdparty', checkThirdParty);
    safe('iframes', checkIframes);
    safe('libraries', checkLibraries);
    safe('secrets', checkSecrets);
    safe('comments', checkComments);
    safe('misc', checkMisc);

    const meta = { origin: location.origin, when: new Date().toISOString(), version: VERSION };
    printReport(meta);

    const report = Object.assign({}, meta, {
      summary: findings.reduce((acc, f) => { acc[f.sev] = (acc[f.sev] || 0) + 1; return acc; }, {}),
      findings,
    });
    try { window.__COMI2_SEC = report; } catch {}
    return report;
  }

  return run();
})();
