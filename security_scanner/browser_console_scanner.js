/* =============================================================================
 * Self Security Scanner — 브라우저 F12 콘솔용 (Browser DevTools Console edition)
 * -----------------------------------------------------------------------------
 * 사용법: 점검할 "내 사이트"를 브라우저에서 연 뒤 F12 → Console 탭에 이 파일
 *         전체를 붙여넣고 Enter. (Chrome이 붙여넣기를 막으면 'allow pasting'을
 *         한 번 입력 후 다시 붙여넣기.)
 *
 *   SITESCAN()                    // 기본: 같은 출처 능동 점검 포함(비파괴 GET)
 *   SITESCAN({ deep:false })      // 수동(passive) 점검만 — 페이로드 전송 안 함
 *   SITESCAN({ includePost:true })// POST 폼까지 능동 점검(부작용 가능, 주의)
 *   SITESCAN({ maxUrls:80 })      // 같은 출처 크롤 상한
 *
 * 결과는 콘솔에 실시간 로그 + 표(console.table)로 뜨고 window.__scan 에 저장됩니다.
 *
 * ⚠️ 본인 소유이거나 점검 권한이 있는 사이트에서만 실행하세요. 능동 점검은
 *    실제 공격성 트래픽(같은 출처)을 보냅니다.
 * ---------------------------------------------------------------------------*/
(function () {
  "use strict";

  const DEFAULTS = { deep: true, includePost: false, maxUrls: 40, rateMs: 120 };

  const SEV = { CRITICAL: 4, HIGH: 3, MEDIUM: 2, LOW: 1, INFO: 0 };
  const SEV_STYLE = {
    CRITICAL: "background:#b00020;color:#fff;font-weight:bold;padding:1px 6px;border-radius:3px",
    HIGH:     "background:#d32f2f;color:#fff;font-weight:bold;padding:1px 6px;border-radius:3px",
    MEDIUM:   "background:#f9a825;color:#000;font-weight:bold;padding:1px 6px;border-radius:3px",
    LOW:      "background:#0277bd;color:#fff;font-weight:bold;padding:1px 6px;border-radius:3px",
    INFO:     "background:#546e7a;color:#fff;padding:1px 6px;border-radius:3px",
  };

  function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

  async function main(userOpts) {
    const opt = Object.assign({}, DEFAULTS, userOpts || {}, window.SITESCAN_OPTS || {});
    const findings = [];
    const seen = new Set();
    const origin = location.origin;
    // Declared here (not inside the active block) so hoisted probe functions
    // can reference it without a temporal-dead-zone error.
    const SQL_ERR = /(you have an error in your sql syntax|warning:\s+mysqli?_|unclosed quotation mark|quoted string not properly terminated|pg_query\(\)|sqlite_error|sqlite3\.OperationalError|ORA-\d{5}|SQLSTATE\[|System\.Data\.SqlClient)/i;

    console.log("%c Self Security Scanner (F12) ", "background:#111;color:#0f0;font-weight:bold;padding:2px 8px");
    console.log("대상 출처: %c" + origin, "font-weight:bold");
    console.log("모드: " + (opt.deep ? "능동(active, 같은 출처 비파괴 GET) + 수동" : "수동(passive)만"));
    console.log("⚠️ 본인 소유/권한 있는 사이트에서만 실행하세요.");

    function add(severity, category, title, detail) {
      detail = detail || {};
      const key = severity + "|" + category + "|" + title + "|" + (detail.url || "") + "|" + (detail.parameter || "");
      if (seen.has(key)) return;
      seen.add(key);
      const f = {
        severity, category, title,
        url: detail.url || location.href,
        parameter: detail.parameter || "",
        evidence: detail.evidence || "",
        remediation: detail.remediation || "",
      };
      findings.push(f);
      const head = "%c" + severity + "%c " + category + ": " + title;
      console.log(head, SEV_STYLE[severity] || "", "color:inherit");
      if (f.parameter) console.log("    parameter:", f.parameter);
      if (f.url && f.url !== location.href) console.log("    url:", f.url);
      if (f.evidence) console.log("    evidence:", f.evidence);
    }

    // helper: same-origin fetch returning {status, headers, text} or null
    async function fetchText(url, init) {
      try {
        const res = await fetch(url, Object.assign({ credentials: "include", cache: "no-store" }, init || {}));
        const text = await res.text();
        return { status: res.status, headers: res.headers, text, url: res.url, res };
      } catch (e) {
        return null;
      }
    }

    // === PASSIVE CHECKS ======================================================

    // 1) HTTPS / transport
    try {
      if (location.protocol !== "https:") {
        add("MEDIUM", "Transport", "사이트가 평문 HTTP로 제공됨", {
          evidence: "location.protocol=" + location.protocol,
          remediation: "HTTPS로 서비스하고 HTTP→HTTPS 리다이렉트 및 HSTS 적용.",
        });
      }
    } catch (e) {}

    // 2) 보안 헤더 (같은 출처 응답에서 읽기 가능한 것만)
    try {
      const r = await fetchText(location.href);
      if (r) {
        const h = r.headers;
        const isHttps = location.protocol === "https:";
        const cspMeta = document.querySelector('meta[http-equiv="Content-Security-Policy" i]');
        if (!h.get("content-security-policy") && !cspMeta) {
          add("MEDIUM", "Security Headers", "CSP(Content-Security-Policy) 없음", {
            remediation: "default-src 'self' 기반의 엄격한 CSP 적용 ('unsafe-inline' 지양).",
          });
        }
        if (!h.get("x-frame-options") && !/frame-ancestors/i.test(h.get("content-security-policy") || "")) {
          add("MEDIUM", "Security Headers", "X-Frame-Options / frame-ancestors 없음 (클릭재킹)", {
            remediation: "X-Frame-Options: DENY 또는 CSP frame-ancestors 적용.",
          });
        }
        if (!h.get("x-content-type-options")) {
          add("LOW", "Security Headers", "X-Content-Type-Options 없음 (MIME 스니핑)", {
            remediation: "X-Content-Type-Options: nosniff 적용.",
          });
        }
        if (!h.get("referrer-policy")) {
          add("LOW", "Security Headers", "Referrer-Policy 없음", {
            remediation: "Referrer-Policy: strict-origin-when-cross-origin 등 적용.",
          });
        }
        if (isHttps && !h.get("strict-transport-security")) {
          add("MEDIUM", "Security Headers", "HSTS 없음", {
            remediation: "Strict-Transport-Security: max-age=31536000; includeSubDomains 적용.",
          });
        }
        const server = h.get("server");
        if (server && /\d+\.\d+/.test(server)) {
          add("LOW", "Information Disclosure", "Server 헤더 버전 노출", {
            evidence: "Server: " + server,
            remediation: "서버 버전 배너 숨기기(server_tokens off 등).",
          });
        }
        const xpb = h.get("x-powered-by");
        if (xpb) {
          add("LOW", "Information Disclosure", "X-Powered-By 노출", {
            evidence: "X-Powered-By: " + xpb, remediation: "X-Powered-By 헤더 제거.",
          });
        }
      }
    } catch (e) {}

    // 3) 쿠키 플래그 (cookieStore 있으면 상세, 없으면 document.cookie로 HttpOnly 부재 추론)
    try {
      if (window.cookieStore && cookieStore.getAll) {
        const cookies = await cookieStore.getAll();
        for (const c of cookies) {
          const problems = [];
          if (location.protocol === "https:" && c.secure === false) problems.push("Secure 없음");
          if (!c.sameSite || c.sameSite === "none") problems.push("SameSite=None/미설정 (CSRF)");
          const sensitive = /sess|token|auth|jwt|sid|login/i.test(c.name);
          // JS로 보이는 쿠키 = HttpOnly 아님
          add(sensitive ? "MEDIUM" : "LOW", "Session Management",
              "쿠키 '" + c.name + "' — JS 접근 가능(HttpOnly 아님)" + (problems.length ? " / " + problems.join(", ") : ""), {
            evidence: "secure=" + c.secure + ", sameSite=" + c.sameSite,
            remediation: "세션 쿠키에 HttpOnly, Secure, SameSite=Lax/Strict 적용.",
          });
        }
      } else if (document.cookie) {
        for (const pair of document.cookie.split(";")) {
          const name = pair.split("=")[0].trim();
          if (!name) continue;
          const sensitive = /sess|token|auth|jwt|sid|login/i.test(name);
          add(sensitive ? "MEDIUM" : "LOW", "Session Management",
              "쿠키 '" + name + "' 가 JS로 읽힘 (HttpOnly 미적용 추정)", {
            remediation: "세션 쿠키에 HttpOnly, Secure, SameSite 적용.",
          });
        }
      }
    } catch (e) {}

    // 4) 혼합 콘텐츠 (https 페이지의 http 리소스)
    try {
      if (location.protocol === "https:") {
        const bad = [];
        document.querySelectorAll("[src],[href],form[action]").forEach((el) => {
          const v = el.getAttribute("src") || el.getAttribute("href") || el.getAttribute("action") || "";
          if (/^http:\/\//i.test(v)) bad.push((el.tagName.toLowerCase()) + " -> " + v);
        });
        if (bad.length) {
          add("MEDIUM", "Mixed Content", bad.length + "개 http 리소스가 https 페이지에 로드됨", {
            evidence: bad.slice(0, 8).join(" | "),
            remediation: "모든 리소스를 https로. upgrade-insecure-requests CSP 지시자 고려.",
          });
        }
      }
    } catch (e) {}

    // 5) 폼: CSRF 토큰 없음 / http 액션 / 비밀번호 필드 autocomplete
    try {
      document.querySelectorAll("form").forEach((form) => {
        const inputs = Array.from(form.querySelectorAll("input,select,textarea"));
        const names = inputs.map((i) => i.name || "");
        const method = (form.getAttribute("method") || "GET").toUpperCase();
        const hasToken = names.some((n) => /csrf|xsrf|token|nonce|authenticity|verification/i.test(n));
        const hasPw = inputs.some((i) => (i.type || "").toLowerCase() === "password");
        if (method === "POST" && !hasToken) {
          add(hasPw ? "LOW" : "MEDIUM", "CSRF", "POST 폼에 반-CSRF 토큰 없음", {
            url: form.action || location.href,
            evidence: "fields=" + JSON.stringify(names.filter(Boolean)),
            remediation: "폼별/세션별 CSRF 토큰 + SameSite 쿠키.",
          });
        }
        if (/^http:\/\//i.test(form.action || "")) {
          add("HIGH", "Transport", "폼이 평문 http로 데이터 전송", {
            url: form.action, remediation: "폼 action을 https로.",
          });
        }
      });
    } catch (e) {}

    // 6) target=_blank + noopener 없음 (탭내빙)
    try {
      let count = 0, ex = "";
      document.querySelectorAll('a[target="_blank"]').forEach((a) => {
        const rel = (a.getAttribute("rel") || "").toLowerCase();
        if (!/noopener/.test(rel)) { count++; if (!ex) ex = a.href; }
      });
      if (count) {
        add("LOW", "Tabnabbing", count + "개 target=_blank 링크에 rel=noopener 없음", {
          evidence: "예: " + ex, remediation: 'rel="noopener noreferrer" 추가.',
        });
      }
    } catch (e) {}

    // 7) 인라인 이벤트 핸들러 / 인라인 script (CSP 저해 신호)
    try {
      let handlers = 0;
      document.querySelectorAll("*").forEach((el) => {
        for (const attr of el.attributes || []) {
          if (/^on/i.test(attr.name)) handlers++;
        }
      });
      const inlineScripts = Array.from(document.scripts).filter((s) => !s.src && s.textContent.trim());
      if (handlers) add("INFO", "CSP Hygiene", handlers + "개 인라인 이벤트 핸들러(onclick 등) 사용", {
        remediation: "인라인 핸들러 제거 → addEventListener; 엄격한 CSP 적용 가능.",
      });
      if (inlineScripts.length) add("INFO", "CSP Hygiene", inlineScripts.length + "개 인라인 <script> 사용", {
        remediation: "인라인 스크립트 외부화 → 'unsafe-inline' 없는 CSP 가능.",
      });
    } catch (e) {}

    // 8) 서드파티 스크립트 (공급망 표면)
    try {
      const third = new Set();
      document.querySelectorAll("script[src]").forEach((s) => {
        try { const u = new URL(s.src, location.href); if (u.origin !== origin) third.add(u.origin); } catch (e) {}
      });
      if (third.size) add("INFO", "Supply Chain", third.size + "개 서드파티 출처에서 스크립트 로드", {
        evidence: Array.from(third).slice(0, 10).join(", "),
        remediation: "필요한 것만 유지, Subresource Integrity(SRI) 적용 고려.",
      });
    } catch (e) {}

    // 9) 노출된 시크릿 (페이지 소스 + 인라인/같은출처 스크립트 본문 스캔)
    try {
      const patterns = [
        [/AKIA[0-9A-Z]{16}/g, "AWS Access Key ID", "HIGH"],
        [/AIza[0-9A-Za-z\-_]{35}/g, "Google API Key", "MEDIUM"],
        [/eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}/g, "JWT 토큰", "MEDIUM"],
        [/xox[baprs]-[0-9A-Za-z\-]{10,}/g, "Slack 토큰", "HIGH"],
        [/gh[pousr]_[0-9A-Za-z]{30,}/g, "GitHub 토큰", "HIGH"],
        [/-----BEGIN (?:RSA|EC|OPENSSH|DSA|PRIVATE) PRIVATE KEY-----/g, "개인키", "CRITICAL"],
        [/(?:sk|rk)_live_[0-9A-Za-z]{20,}/g, "Stripe 라이브 키", "CRITICAL"],
        [/(?:api[_-]?key|secret|passwd|password|access[_-]?token)\s*[:=]\s*['"][^'"\s]{8,}['"]/gi, "하드코딩된 시크릿(추정)", "MEDIUM"],
      ];
      let corpus = document.documentElement.outerHTML;
      for (const s of document.scripts) { if (!s.src && s.textContent) corpus += "\n" + s.textContent; }
      // 같은 출처 외부 스크립트 본문도 가져와서 스캔
      const sameOriginScripts = Array.from(document.querySelectorAll("script[src]"))
        .map((s) => { try { return new URL(s.src, location.href); } catch (e) { return null; } })
        .filter((u) => u && u.origin === origin).slice(0, 15);
      for (const u of sameOriginScripts) {
        const r = await fetchText(u.href); if (r) corpus += "\n" + r.text;
        await sleep(20);
      }
      const hits = new Map();
      for (const [rx, label, sev] of patterns) {
        const m = corpus.match(rx);
        if (m) hits.set(label, [sev, Array.from(new Set(m)).slice(0, 3)]);
      }
      for (const [label, [sev, samples]] of hits) {
        add(sev, "Secret Exposure", "노출된 " + label, {
          evidence: samples.map((s) => (s.length > 24 ? s.slice(0, 24) + "…" : s)).join(", "),
          remediation: "클라이언트 코드에서 시크릿 제거, 노출된 키 즉시 폐기/교체.",
        });
      }
    } catch (e) {}

    // 10) localStorage / sessionStorage 민감정보
    try {
      for (const [store, name] of [[localStorage, "localStorage"], [sessionStorage, "sessionStorage"]]) {
        for (let i = 0; i < store.length; i++) {
          const k = store.key(i); const v = store.getItem(k) || "";
          if (/token|jwt|secret|password|passwd|auth|session|api[_-]?key|credit|card/i.test(k) ||
              /eyJ[A-Za-z0-9_\-]{10,}\./.test(v)) {
            add("MEDIUM", "Client Storage", name + " 에 민감 데이터로 보이는 항목: '" + k + "'", {
              evidence: "value~" + v.slice(0, 30) + (v.length > 30 ? "…" : ""),
              remediation: "토큰/세션은 XSS로 탈취 가능한 웹스토리지 대신 HttpOnly 쿠키에 저장.",
            });
          }
        }
      }
    } catch (e) {}

    // 11) DOM XSS 싱크 사용 흔적 (휴리스틱)
    try {
      let corpus = "";
      for (const s of document.scripts) { if (!s.src && s.textContent) corpus += "\n" + s.textContent; }
      const sinks = [];
      if (/\.innerHTML\s*=/.test(corpus)) sinks.push("innerHTML=");
      if (/document\.write\s*\(/.test(corpus)) sinks.push("document.write()");
      if (/\beval\s*\(/.test(corpus)) sinks.push("eval()");
      if (/location\.(hash|search|href)/.test(corpus) && /innerHTML|document\.write|eval/.test(corpus))
        sinks.push("location.* → 싱크");
      if (sinks.length) add("LOW", "DOM XSS (heuristic)", "위험한 DOM 싱크 사용: " + sinks.join(", "), {
        remediation: "innerHTML 대신 textContent, eval 금지, 사용자 입력을 싱크에 직접 넣지 않기.",
      });
    } catch (e) {}

    // === ACTIVE (same-origin, 비파괴 GET) ===================================
    if (opt.deep) {
      try {
        console.log("%c[*] 능동 점검(같은 출처) 시작…", "color:#888");
        const points = await discoverPoints(opt);
        console.log("    점검 대상 파라미터: " + points.length + "개");
        for (const p of points) {
          await activeProbe(p);
          await sleep(opt.rateMs);
        }
        await probeSensitiveFiles();
      } catch (e) { console.log("active error:", e); }
    }

    // 같은 출처에서 파라미터 있는 URL / GET 폼 수집
    async function discoverPoints(opt) {
      const pts = [];
      const urlSet = new Set();
      function addUrl(u) {
        try {
          const url = new URL(u, location.href);
          if (url.origin !== origin) return;
          if (!url.search) return;
          const sig = url.pathname + "?" + Array.from(url.searchParams.keys()).sort().join(",");
          if (urlSet.has(sig)) return;
          urlSet.add(sig);
          for (const name of new Set(url.searchParams.keys())) {
            pts.push({ method: "GET", base: url.origin + url.pathname, params: url.searchParams, param: name });
          }
        } catch (e) {}
      }
      // 현재 URL
      addUrl(location.href);
      // 페이지 내 같은 출처 링크
      document.querySelectorAll("a[href]").forEach((a) => addUrl(a.href));
      // GET 폼
      document.querySelectorAll("form").forEach((form) => {
        const method = (form.getAttribute("method") || "GET").toUpperCase();
        if (method !== "GET" && !opt.includePost) return;
        try {
          const action = new URL(form.action || location.href, location.href);
          if (action.origin !== origin) return;
          const params = new URLSearchParams(action.search);
          form.querySelectorAll("input[name],select[name],textarea[name]").forEach((i) => {
            params.set(i.name, i.value || "1");
          });
          form.querySelectorAll("input[name],select[name],textarea[name]").forEach((i) => {
            pts.push({ method, base: action.origin + action.pathname, params, param: i.name });
          });
        } catch (e) {}
      });
      return pts.slice(0, opt.maxUrls);
    }

    function buildUrl(p, value) {
      const params = new URLSearchParams(p.params.toString());
      params.set(p.param, value);
      return p.base + "?" + params.toString();
    }

    async function activeProbe(p) {
      const orig = p.params.get(p.param) || "1";

      // -- SQLi (error-based)
      for (const pay of ["'", "\"", "')", "';"]) {
        const r = await fetchText(buildUrl(p, orig + pay));
        if (r && SQL_ERR.test(r.text)) {
          add("CRITICAL", "SQL Injection", "SQL 인젝션(에러 기반)", {
            url: p.base, parameter: p.param,
            evidence: "payload=" + JSON.stringify(pay) + " 로 DB 에러 반환",
            remediation: "파라미터라이즈드 쿼리(프리페어드 스테이트먼트) 사용, 최소권한 DB 계정.",
          });
          return;
        }
        await sleep(30);
      }

      // -- Reflected XSS
      const marker = "xz" + Math.random().toString(16).slice(2, 8);
      const probe = await fetchText(buildUrl(p, marker));
      if (probe && probe.text.indexOf(marker) !== -1) {
        for (const pay of ['"><svg/onload=alert(1)>', "<" + marker + ">", "'\"><img src=x onerror=1>"]) {
          const r = await fetchText(buildUrl(p, pay));
          if (r && r.text.indexOf(pay) !== -1) {
            const ct = (r.headers.get("content-type") || "").toLowerCase();
            add(ct.indexOf("html") !== -1 ? "HIGH" : "LOW", "Cross-Site Scripting",
                ct.indexOf("html") !== -1 ? "반사형 XSS" : "입력 반사(비 HTML)", {
              url: p.base, parameter: p.param,
              evidence: "payload " + JSON.stringify(pay) + " 가 인코딩 없이 반사됨",
              remediation: "출력 시 컨텍스트별 인코딩, 엄격한 CSP.",
            });
            break;
          }
          await sleep(30);
        }
      }

      // -- Path traversal / LFI
      if (/file|path|page|doc|dir|load|read|view|include|template|download|url|src/i.test(p.param)) {
        for (const pay of ["../../../../../../../../etc/passwd", "..%2f..%2f..%2f..%2f..%2f..%2fetc%2fpasswd"]) {
          const r = await fetchText(buildUrl(p, pay));
          if (r && /root:.*:0:0:/.test(r.text)) {
            add("CRITICAL", "Path Traversal / LFI", "경로 탐색 / 로컬 파일 인클루전", {
              url: p.base, parameter: p.param,
              evidence: "payload " + JSON.stringify(pay) + " 가 /etc/passwd 내용 반환",
              remediation: "사용자 입력으로 파일 경로 구성 금지, 허용목록 기반.",
            });
            return;
          }
          await sleep(30);
        }
      }

      // -- OS 명령어 주입 (출력 기반)
      {
        const tok = "ci" + Math.random().toString(16).slice(2, 10);
        for (const tpl of [";echo " + tok + ";", "|echo " + tok, "&&echo " + tok, "`echo " + tok + "`", "$(echo " + tok + ")"]) {
          const r = await fetchText(buildUrl(p, orig + tpl));
          // 토큰이 출력되되, 페이로드 문자열 자체가 그대로 반사된 게 아니어야 함
          if (r && r.text.indexOf(tok) !== -1 && r.text.indexOf("echo " + tok) === -1) {
            add("CRITICAL", "Command Injection", "OS 명령어 주입(출력 기반)", {
              url: p.base, parameter: p.param,
              evidence: "payload " + JSON.stringify(tpl) + " 로 명령 출력에 토큰 " + tok + " 등장",
              remediation: "쉘에 사용자 입력 전달 금지, 인자 배열 API 사용, 최소권한 계정.",
            });
            return;
          }
          await sleep(30);
        }
      }
    }

    // 같은 출처 민감 파일 노출 점검
    async function probeSensitiveFiles() {
      const targets = [
        [".env", "CRITICAL", /(APP_KEY|DB_PASSWORD|SECRET|API_KEY|AWS_)/],
        [".git/config", "HIGH", /\[core\]|repositoryformatversion/],
        [".git/HEAD", "HIGH", /ref:\s*refs\//],
        ["backup.sql", "CRITICAL", /(INSERT INTO|CREATE TABLE)/],
        ["config.php.bak", "CRITICAL", /<\?php|password/i],
        ["wp-config.php.bak", "CRITICAL", /DB_PASSWORD/],
        ["phpinfo.php", "HIGH", /phpinfo\(\)|PHP Version/],
        [".DS_Store", "LOW", /Bud1/],
        ["/server-status", "MEDIUM", /Apache Server Status/],
        ["/actuator/env", "HIGH", /propertySources|systemProperties/],
      ];
      // 소프트 404 기준선
      let base404len = -1;
      const rnd = await fetchText(origin + "/zz_" + Math.random().toString(16).slice(2) + "_notfound");
      if (rnd && rnd.status === 200) base404len = rnd.text.length;
      for (const [path, sev, sig] of targets) {
        const url = origin + "/" + path.replace(/^\//, "");
        const r = await fetchText(url);
        if (!r || r.status !== 200) { await sleep(40); continue; }
        if (base404len >= 0 && Math.abs(r.text.length - base404len) < 32) { await sleep(40); continue; }
        if (sig && !sig.test(r.text)) { await sleep(40); continue; }
        add(sev, "Sensitive Exposure", "민감 파일 노출: " + path, {
          url, evidence: "HTTP 200, " + r.text.length + " bytes",
          remediation: "웹 루트에서 제거하거나 접근 차단, 노출된 시크릿 즉시 교체.",
        });
        await sleep(40);
      }
    }

    // === SUMMARY =============================================================
    findings.sort((a, b) => SEV[b.severity] - SEV[a.severity]);
    const counts = { CRITICAL: 0, HIGH: 0, MEDIUM: 0, LOW: 0, INFO: 0 };
    findings.forEach((f) => counts[f.severity]++);

    console.log("%c──────────────────────────────────────────────", "color:#888");
    console.log("%c 스캔 완료 %c  " + origin, "background:#111;color:#0f0;font-weight:bold;padding:2px 8px", "");
    console.log(
      "%cCRITICAL " + counts.CRITICAL + "  %cHIGH " + counts.HIGH + "  %cMEDIUM " + counts.MEDIUM + "  %cLOW " + counts.LOW + "  %cINFO " + counts.INFO,
      "color:#b00020;font-weight:bold", "color:#d32f2f;font-weight:bold",
      "color:#f9a825;font-weight:bold", "color:#0277bd;font-weight:bold", "color:#546e7a"
    );
    console.log("총 " + findings.length + "건. 상세 표 ↓ (window.__scan 에도 저장됨)");
    try { console.table(findings.map((f) => ({ severity: f.severity, category: f.category, title: f.title, param: f.parameter }))); } catch (e) {}

    window.__scan = { origin, when: new Date().toISOString(), counts, findings };
    console.log("전체 결과 JSON: %ccopy(JSON.stringify(window.__scan, null, 2))", "font-weight:bold");
    return window.__scan;
  }

  // 전역 노출 + 자동 실행
  window.SITESCAN = main;
  main();
})();
