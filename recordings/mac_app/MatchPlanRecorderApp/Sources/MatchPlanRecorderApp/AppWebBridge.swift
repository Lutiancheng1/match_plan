import Foundation
import Network
import WebKit

@MainActor
final class AppWebBridge {
    static let shared = AppWebBridge()
    private let schedulesLiveURL = URL(string: "https://sftraders.live/schedules/live")!

    private var listener: NWListener?
    private weak var primaryWebView: WKWebView?
    private weak var fallbackWebView: WKWebView?
    let port: UInt16 = 18765

    private init() {}

    var baseURL: URL {
        URL(string: "http://127.0.0.1:\(port)")!
    }

    var isReady: Bool {
        activeWebView != nil
    }

    private var activeWebView: WKWebView? {
        primaryWebView ?? fallbackWebView
    }

    private func webViewsInPriorityOrder() -> [WKWebView] {
        var views: [WKWebView] = []
        if let primaryWebView {
            views.append(primaryWebView)
        }
        if let fallbackWebView, fallbackWebView !== primaryWebView {
            views.append(fallbackWebView)
        }
        return views
    }

    func register(webView: WKWebView, preferred: Bool) {
        if preferred {
            self.primaryWebView = webView
        } else if fallbackWebView == nil || fallbackWebView === webView {
            self.fallbackWebView = webView
        }
        if listener == nil {
            startServer()
        }
    }

    func reloadRegisteredWebViews() {
        primaryWebView?.reload()
        if let fallbackWebView, fallbackWebView !== primaryWebView {
            fallbackWebView.reload()
        }
    }

    func ensureSchedulesLiveLoaded() {
        guard let webView = activeWebView else { return }
        let current = webView.url?.absoluteString ?? ""
        if current.isEmpty || current == "about:blank" {
            webView.load(URLRequest(url: schedulesLiveURL))
            return
        }
        guard let url = webView.url else { return }
        let host = url.host?.lowercased() ?? ""
        let path = url.path.lowercased()
        if host.contains("sftraders.live"), path == "/schedules/live" {
            return
        }
        if host.contains("sftraders.live"), path == "/login" {
            return
        }
        webView.load(URLRequest(url: schedulesLiveURL))
    }

    private func startServer() {
        do {
            let listener = try NWListener(using: .tcp, on: NWEndpoint.Port(rawValue: port)!)
            listener.newConnectionHandler = { [weak self] connection in
                connection.start(queue: .global(qos: .utility))
                Task { @MainActor in
                    self?.handle(connection: connection)
                }
            }
            listener.start(queue: .global(qos: .utility))
            self.listener = listener
        } catch {
            print("AppWebBridge start error: \(error)")
        }
    }

    private func handle(connection: NWConnection) {
        connection.receive(minimumIncompleteLength: 1, maximumLength: 32768) { [weak self] data, _, _, _ in
            guard let self, let data, let request = String(data: data, encoding: .utf8) else {
                connection.cancel()
                return
            }
            Task { @MainActor in
                let response = await self.route(request: request)
                let payload = self.httpResponse(status: 200, body: response)
                connection.send(content: payload, completion: .contentProcessed { _ in
                    connection.cancel()
                })
            }
        }
    }

    private func route(request: String) async -> Data {
        let firstLine = request.split(separator: "\r\n").first.map(String.init) ?? ""
        let path = firstLine.split(separator: " ").dropFirst().first.map(String.init) ?? "/"
        guard let url = URL(string: "http://127.0.0.1\(path)") else {
            return encode(["ok": false, "error": "invalid request"])
        }
        switch url.path {
        case "/health":
            return encode([
                "ok": true,
                "webViewReady": isReady,
                "baseURL": baseURL.absoluteString,
            ])
        case "/page-state":
            let payload = await pageState()
            return encode(payload)
        case "/discover-live":
            let payload = await discoverLiveCandidates()
            return encode(payload)
        case "/fetch-watch":
            let watchURL = URLComponents(url: url, resolvingAgainstBaseURL: false)?
                .queryItems?
                .first(where: { $0.name == "watch_url" })?
                .value ?? ""
            let payload = await fetchWatchHTML(watchURL: watchURL)
            return encode(payload)
        case "/extract-credentials":
            let payload = await extractCredentials()
            return encode(payload)
        case "/auto-login":
            let params = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems ?? []
            let username = params.first(where: { $0.name == "username" })?.value ?? ""
            let password = params.first(where: { $0.name == "password" })?.value ?? ""
            let payload = await autoLogin(username: username, password: password)
            return encode(payload)
        default:
            return encode(["ok": false, "error": "not found", "path": url.path])
        }
    }

    private func discoverLiveCandidates() async -> [String: Any] {
        guard let webView = await selectBestWebView() else {
            return ["ok": false, "error": "webview not ready", "source": "app_web_bridge"]
        }
        let js = """
        (() => {
          const liveTab = document.getElementById("schedules-content-live-tab");
          if (liveTab) { liveTab.click(); }
          const livePane = document.getElementById("schedules-content-live");
          if (!livePane) {
            return JSON.stringify({
              ok: false,
              error: "NO_LIVE_PANE",
              source: "app_web_bridge",
              currentURL: location.href,
              title: document.title,
              readyState: document.readyState
            });
          }
          const rows = Array.from(livePane.querySelectorAll('tr, .has-data, .event-row, .match-row, li'))
            .filter(el => el.querySelector('a[href*="/watch"]'));
          const seen = new Set();
          const items = [];
          for (const row of rows) {
            const link = row.querySelector('a[href*="/watch"]');
            if (!link || !link.href || link.href.includes("withChart=1") || seen.has(link.href)) { continue; }
            seen.add(link.href);
            const parts = (row.innerText || "")
              .split(/\\n+/)
              .map(x => x.trim())
              .filter(Boolean);
            const xIndex = parts.findIndex(x => /^x$/i.test(x));
            let home = "";
            let away = "";
            let league = "";
            let kickoff = "";
            if (xIndex >= 0) {
              home = parts[xIndex - 1] || "";
              away = parts[xIndex + 1] || "";
              league = parts[xIndex + 2] || "";
              kickoff = parts[xIndex + 3] || "";
            } else {
              home = parts[0] || "";
              league = parts[1] || "";
              kickoff = parts[2] || "";
            }
            items.push({
              href: link.href,
              home,
              away,
              league,
              kickoff,
              raw_text: (row.innerText || "").trim(),
            });
          }
          return JSON.stringify({ ok: true, source: "app_web_bridge", items });
        })();
        """
        do {
            let result = try await evaluateAsyncJS(webView: webView, script: js)
            if let data = result.data(using: .utf8),
               let dict = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                return dict
            }
            return ["ok": false, "error": "unexpected discover payload", "source": "app_web_bridge"]
        } catch {
            return ["ok": false, "error": error.localizedDescription, "source": "app_web_bridge"]
        }
    }

    private func pageState() async -> [String: Any] {
        guard let webView = await selectBestWebView() else {
            return [
                "ok": false,
                "webViewReady": false,
                "source": "app_web_bridge",
                "error": "webview not ready",
            ]
        }
        return await evaluatePageState(webView: webView) ?? [
            "ok": false,
            "webViewReady": true,
            "source": "app_web_bridge",
            "error": "unexpected page-state payload",
        ]
    }

    private func fetchWatchHTML(watchURL: String) async -> [String: Any] {
        guard let webView = await selectBestWebView() else {
            return ["ok": false, "status": 0, "error": "webview not ready", "watch_url": watchURL, "source": "app_web_bridge"]
        }
        let js = """
        (() => {
          const target = \(jsonLiteral(watchURL));
          try {
            const xhr = new XMLHttpRequest();
            xhr.open('GET', target, false);
            xhr.withCredentials = true;
            xhr.setRequestHeader('Cache-Control', 'no-cache');
            xhr.send(null);
            return JSON.stringify({
              ok: (xhr.status || 0) >= 200 && (xhr.status || 0) < 300,
              status: xhr.status || 0,
              responseURL: xhr.responseURL || '',
              html: xhr.responseText || '',
              watch_url: target
            });
          } catch (err) {
            return JSON.stringify({
              ok: false,
              status: 0,
              responseURL: '',
              html: '',
              watch_url: target,
              error: String(err || '')
            });
          }
        })();
        """
        do {
            let result = try await evaluateAsyncJS(webView: webView, script: js)
            if let data = result.data(using: .utf8),
               let dict = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                var payload = dict
                payload["source"] = "app_web_bridge"
                return payload
            }
            return ["ok": false, "status": 0, "error": "unexpected bridge payload", "watch_url": watchURL, "source": "app_web_bridge"]
        } catch {
            return ["ok": false, "status": 0, "error": error.localizedDescription, "watch_url": watchURL, "source": "app_web_bridge"]
        }
    }

    private func autoLogin(username: String, password: String) async -> [String: Any] {
        guard !username.isEmpty, !password.isEmpty else {
            return ["ok": false, "error": "username and password required", "source": "app_web_bridge"]
        }
        guard let webView = await selectBestWebView() else {
            return ["ok": false, "error": "webview not ready", "source": "app_web_bridge"]
        }

        // First ensure we're on the login page
        let currentURL = webView.url?.absoluteString ?? ""
        if !currentURL.contains("login") && !currentURL.contains("sftraders") {
            webView.load(URLRequest(url: URL(string: "https://sftraders.live/login")!))
            try? await Task.sleep(nanoseconds: 2_000_000_000)
        }

        let js = """
        return await new Promise((resolve) => {
          const timeout = setTimeout(() => {
            resolve(JSON.stringify({ok: false, error: "login_form_timeout", url: location.href}));
          }, 10000);

          function tryLogin() {
            // Try common selectors for username/password fields
            const userInput = document.querySelector('input[name="username"], input[type="text"][name*="user"], #username, input[placeholder*="用户"], input[placeholder*="帐号"], input[placeholder*="账号"]');
            const passInput = document.querySelector('input[name="password"], input[type="password"], #password');
            const submitBtn = document.querySelector('button[type="submit"], input[type="submit"], .login-btn, .btn-login, button.btn-primary');

            if (!userInput || !passInput) {
              return false;
            }

            // Fill in credentials
            const nativeInputValueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
            nativeInputValueSetter.call(userInput, \(jsonLiteral(username)));
            userInput.dispatchEvent(new Event('input', {bubbles: true}));
            userInput.dispatchEvent(new Event('change', {bubbles: true}));

            nativeInputValueSetter.call(passInput, \(jsonLiteral(password)));
            passInput.dispatchEvent(new Event('input', {bubbles: true}));
            passInput.dispatchEvent(new Event('change', {bubbles: true}));

            // Click submit
            if (submitBtn) {
              submitBtn.click();
            } else {
              // Try submitting the form directly
              const form = userInput.closest('form');
              if (form) { form.submit(); }
            }

            clearTimeout(timeout);
            setTimeout(() => {
              resolve(JSON.stringify({
                ok: true,
                url: location.href,
                title: document.title,
                submitted: true
              }));
            }, 2000);
            return true;
          }

          // Try immediately, then retry after short delays
          if (!tryLogin()) {
            let retries = 0;
            const interval = setInterval(() => {
              retries++;
              if (tryLogin() || retries > 10) {
                clearInterval(interval);
                if (retries > 10) {
                  clearTimeout(timeout);
                  resolve(JSON.stringify({
                    ok: false,
                    error: "login_form_not_found",
                    url: location.href,
                    title: document.title,
                    html_snippet: document.body ? document.body.innerHTML.substring(0, 500) : ""
                  }));
                }
              }
            }, 500);
          }
        });
        """

        do {
            let result = try await callAsyncJS(webView: webView, script: js)
            if let data = result.data(using: .utf8),
               let dict = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                var payload = dict
                payload["source"] = "app_web_bridge"
                return payload
            }
            return ["ok": false, "error": "unexpected response", "source": "app_web_bridge"]
        } catch {
            return ["ok": false, "error": error.localizedDescription, "source": "app_web_bridge"]
        }
    }

    private func extractCredentials() async -> [String: Any] {
        guard let webView = await selectBestWebView() else {
            return ["ok": false, "error": "webview not ready", "source": "app_web_bridge"]
        }

        // Step 1: Extract cookies from WKHTTPCookieStore
        let cookieString = await withCheckedContinuation { (continuation: CheckedContinuation<String, Never>) in
            webView.configuration.websiteDataStore.httpCookieStore.getAllCookies { cookies in
                let relevant = cookies.filter { cookie in
                    let domain = cookie.domain.lowercased()
                    return domain.contains("sftraders") || domain.contains("112.121")
                }
                let parts = relevant.map { "\($0.name)=\($0.value)" }
                continuation.resume(returning: parts.joined(separator: "; "))
            }
        }

        // Step 2: Intercept a live feed POST request by hooking XHR, then await via callAsyncJavaScript
        let js = """
        return await new Promise((resolve) => {
          const timeout = setTimeout(() => {
            resolve(JSON.stringify({
              ok: false,
              error: "timeout_waiting_for_feed_request",
              cookie: document.cookie
            }));
          }, 8000);

          const origOpen = XMLHttpRequest.prototype.open;
          const origSend = XMLHttpRequest.prototype.send;
          let captured = false;

          XMLHttpRequest.prototype.open = function(method, url, ...rest) {
            this._mp_method = method;
            this._mp_url = url;
            return origOpen.call(this, method, url, ...rest);
          };

          XMLHttpRequest.prototype.send = function(body) {
            if (!captured && this._mp_method === 'POST' && body) {
              const bodyStr = String(body || '');
              const urlStr = String(this._mp_url || '');
              if ((bodyStr.includes('showtype=live') || bodyStr.includes('rtype=rb') || bodyStr.includes('get_game_list'))
                  && (urlStr.includes('transform.php') || urlStr.includes('112.121'))) {
                captured = true;
                clearTimeout(timeout);
                XMLHttpRequest.prototype.open = origOpen;
                XMLHttpRequest.prototype.send = origSend;
                resolve(JSON.stringify({
                  ok: true,
                  feed_url: urlStr,
                  post_body: bodyStr,
                  cookie: document.cookie
                }));
              }
            }
            return origSend.call(this, body);
          };

          // Trigger a page data refresh by clicking the live tab
          const liveTab = document.getElementById("schedules-content-live-tab");
          if (liveTab) { liveTab.click(); }
        });
        """

        do {
            let result = try await callAsyncJS(webView: webView, script: js)
            if let data = result.data(using: .utf8),
               let dict = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                var payload = dict
                if !cookieString.isEmpty {
                    payload["cookie"] = cookieString
                }
                payload["source"] = "app_web_bridge"
                return payload
            }
            var fallback: [String: Any] = [
                "ok": !cookieString.isEmpty,
                "cookie": cookieString,
                "source": "app_web_bridge",
            ]
            if cookieString.isEmpty { fallback["error"] = "no cookies found" }
            return fallback
        } catch {
            var fallback: [String: Any] = [
                "ok": !cookieString.isEmpty,
                "cookie": cookieString,
                "source": "app_web_bridge",
            ]
            fallback["error"] = error.localizedDescription
            return fallback
        }
    }

    private func evaluateAsyncJS(webView: WKWebView, script: String) async throws -> String {
        try await withCheckedThrowingContinuation { continuation in
            webView.evaluateJavaScript(script) { result, error in
                if let error {
                    continuation.resume(throwing: error)
                } else {
                    continuation.resume(returning: result as? String ?? "{}")
                }
            }
        }
    }

    private func callAsyncJS(webView: WKWebView, script: String) async throws -> String {
        let result = try await webView.callAsyncJavaScript(
            script,
            arguments: [:],
            in: nil,
            contentWorld: .page
        )
        return result as? String ?? "{}"
    }

    private func evaluatePageState(webView: WKWebView) async -> [String: Any]? {
        let js = """
        (() => {
          const livePane = document.getElementById("schedules-content-live");
          const rows = livePane
            ? Array.from(livePane.querySelectorAll('tr, .has-data, .event-row, .match-row, li'))
                .filter(el => el.querySelector('a[href*="/watch"]'))
            : [];
          const loginRequired = /login|sign in|entrar/i.test(document.title || '')
            || /\\/login(\\/|$|\\?)/i.test(location.pathname || '');
          return JSON.stringify({
            ok: true,
            webViewReady: true,
            source: "app_web_bridge",
            currentURL: location.href,
            title: document.title,
            readyState: document.readyState,
            hasLivePane: !!livePane,
            loginRequired,
            liveCandidateCount: rows.length
          });
        })();
        """
        do {
            let result = try await evaluateAsyncJS(webView: webView, script: js)
            if let data = result.data(using: .utf8),
               let dict = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                return dict
            }
        } catch {}
        return nil
    }

    private func selectBestWebView() async -> WKWebView? {
        let candidates = webViewsInPriorityOrder()
        if candidates.isEmpty {
            return nil
        }
        var fallback: WKWebView?
        for webView in candidates {
            guard let payload = await evaluatePageState(webView: webView) else {
                continue
            }
            let currentURL = String(describing: payload["currentURL"] ?? "")
            let hasLivePane = (payload["hasLivePane"] as? Bool) ?? false
            let loginRequired = (payload["loginRequired"] as? Bool) ?? false
            if fallback == nil {
                fallback = webView
            }
            if currentURL != "about:blank" && (!loginRequired || hasLivePane) {
                return webView
            }
        }
        return fallback ?? candidates.first
    }

    private func httpResponse(status: Int, body: Data) -> Data {
        var headers = "HTTP/1.1 \(status) OK\r\n"
        headers += "Content-Type: application/json; charset=utf-8\r\n"
        headers += "Content-Length: \(body.count)\r\n"
        headers += "Connection: close\r\n\r\n"
        var data = Data(headers.utf8)
        data.append(body)
        return data
    }

    private func encode(_ payload: [String: Any]) -> Data {
        let data = try? JSONSerialization.data(withJSONObject: payload, options: [.prettyPrinted])
        return data ?? Data("{}".utf8)
    }

    private func jsonLiteral(_ value: String) -> String {
        let data = try? JSONSerialization.data(withJSONObject: [value], options: [])
        guard let data, let string = String(data: data, encoding: .utf8) else {
            return "\"\""
        }
        return String(string.dropFirst().dropLast())
    }
}
