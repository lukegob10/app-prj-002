(function (root, factory) {
  "use strict";
  var api = factory(root);
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.AgoraHost = api;
})(typeof window !== "undefined" ? window : null, function (window) {
  "use strict";

  var PROTOCOL = "agora.viewer/1";
  var HANDSHAKE_TIMEOUT_MS = 20000;
  var API_TIMEOUT_MS = 30000;
  var MAX_INFLIGHT = 16;
  var ALLOWED_CAPABILITIES = ["csv.read", "records.read", "records.write", "sources.read"];
  var METHOD_CAPABILITY = {
    csv: "csv.read",
    "records.list": "records.read",
    "records.get": "records.read",
    "records.create": "records.write",
    "records.update": "records.write",
    "records.delete": "records.write",
    "sources.list": "sources.read",
    "sources.catalogs": "sources.read",
    "sources.schemas": "sources.read",
    "sources.tables": "sources.read",
    "sources.rows": "sources.read"
  };

  function AgoraHostError(code, message, details, status) {
    this.name = "AgoraHostError";
    this.code = code || "AGORA_ERROR";
    this.message = message || "The Agora host request failed.";
    this.details = details;
    this.status = status;
    if (Error.captureStackTrace) Error.captureStackTrace(this, AgoraHostError);
  }
  AgoraHostError.prototype = Object.create(Error.prototype);
  AgoraHostError.prototype.constructor = AgoraHostError;

  function fail(code, message, status, details) {
    return new AgoraHostError(code, message, details, status);
  }

  function isPlainObject(value) {
    if (!value || typeof value !== "object" || Array.isArray(value)) return false;
    var prototype = Object.getPrototypeOf(value);
    return prototype === Object.prototype || prototype === null;
  }

  function requireObject(value, label) {
    if (!isPlainObject(value)) throw fail("INVALID_ARGUMENT", label + " must be an object.");
    return value;
  }

  function requireString(value, label, maxLength) {
    if (typeof value !== "string" || !value.trim() || value.length > (maxLength || 500)) {
      throw fail("INVALID_ARGUMENT", label + " must be a non-empty string.");
    }
    return value;
  }

  function assertKeys(value, allowed, required, label) {
    requireObject(value, label);
    Object.keys(value).forEach(function (key) {
      if (allowed.indexOf(key) < 0) throw fail("INVALID_ARGUMENT", label + " contains an unsupported field.");
    });
    (required || []).forEach(function (key) {
      if (!Object.prototype.hasOwnProperty.call(value, key)) throw fail("INVALID_ARGUMENT", label + " is missing " + key + ".");
    });
  }

  function copyJson(value, label, maxCharacters) {
    var serialized;
    try { serialized = JSON.stringify(value); }
    catch (_) { throw fail("INVALID_ARGUMENT", label + " must contain JSON-compatible data."); }
    if (serialized === undefined || serialized.length > maxCharacters) {
      throw fail("INVALID_ARGUMENT", label + " must contain JSON-compatible data within the size limit.");
    }
    try { return JSON.parse(serialized); }
    catch (_) { throw fail("INVALID_ARGUMENT", label + " must contain JSON-compatible data."); }
  }

  function normaliseCapabilities(input) {
    if (input === undefined) input = [];
    if (!Array.isArray(input)) throw fail("INVALID_ARGUMENT", "capabilities must be an explicit array.");
    var seen = Object.create(null);
    input.forEach(function (capability) {
      if (typeof capability !== "string" || ALLOWED_CAPABILITIES.indexOf(capability) < 0 || seen[capability]) {
        throw fail("INVALID_ARGUMENT", "capabilities contains an unsupported or duplicate capability.");
      }
      seen[capability] = true;
    });
    return Object.keys(seen).sort();
  }

  function makeScope(projectId, versionId, capabilities) {
    return Object.freeze({
      project_id: requireString(projectId, "projectId", 200),
      version_id: requireString(versionId, "versionId", 200),
      capabilities: Object.freeze(normaliseCapabilities(capabilities))
    });
  }

  function parseScope(value) {
    if (!isPlainObject(value)) return null;
    var keys = Object.keys(value).sort();
    if (keys.join(",") !== "capabilities,project_id,version_id") return null;
    if (typeof value.project_id !== "string" || !value.project_id || value.project_id.length > 200) return null;
    if (typeof value.version_id !== "string" || !value.version_id || value.version_id.length > 200) return null;
    if (!Array.isArray(value.capabilities) || value.capabilities.length > ALLOWED_CAPABILITIES.length) return null;
    try { return makeScope(value.project_id, value.version_id, value.capabilities); }
    catch (_) { return null; }
  }

  function sameScope(left, right) {
    return !!left && !!right && left.project_id === right.project_id &&
      left.version_id === right.version_id && left.capabilities.length === right.capabilities.length &&
      left.capabilities.every(function (value, index) { return value === right.capabilities[index]; });
  }

  function constantTimeEqual(left, right) {
    if (typeof left !== "string" || typeof right !== "string" || left.length !== right.length) return false;
    var difference = 0;
    for (var index = 0; index < left.length; index += 1) difference |= left.charCodeAt(index) ^ right.charCodeAt(index);
    return difference === 0;
  }

  function randomNonce() {
    if (!window || !window.crypto || typeof window.crypto.getRandomValues !== "function") {
      throw fail("SECURE_RANDOM_UNAVAILABLE", "The browser cannot create a secure viewer connection.");
    }
    var bytes = new Uint8Array(18);
    window.crypto.getRandomValues(bytes);
    var binary = "";
    for (var index = 0; index < bytes.length; index += 1) binary += String.fromCharCode(bytes[index]);
    return window.btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
  }

  function normaliseApiBase(value) {
    var apiBase = value === undefined ? "/api" : value;
    if (typeof apiBase !== "string" || !apiBase.startsWith("/") || apiBase.startsWith("//") || /[?#\\]/.test(apiBase)) {
      throw fail("INVALID_ARGUMENT", "apiBase must be a same-origin URL path.");
    }
    return apiBase.replace(/\/$/, "");
  }

  function buildApiUrl(path, apiBase) {
    var url = new URL(apiBase + path, window.location.origin);
    if (url.origin !== window.location.origin) throw fail("INVALID_API_URL", "The Agora host may call only its own API origin.");
    return url.href;
  }

  function toPathSegment(value, label) {
    return encodeURIComponent(requireString(value, label));
  }

  function assertRevision(value) {
    if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 1) {
      throw fail("INVALID_ARGUMENT", "expected_revision must be a positive integer.");
    }
    return value;
  }

  function makeFetchInit(method, body, csrfToken, timeoutSignal) {
    var headers = { Accept: method === "GET" && body === null ? "text/csv, application/json" : "application/json" };
    if (body !== null) headers["Content-Type"] = "application/json";
    if (csrfToken) headers["X-CSRF-Token"] = csrfToken;
    return {
      method: method,
      mode: "same-origin",
      credentials: "same-origin",
      redirect: "error",
      cache: "no-store",
      headers: headers,
      body: body === null ? undefined : JSON.stringify(body),
      signal: timeoutSignal
    };
  }

  function responseError(response) {
    return response.text().then(function (text) {
      var envelope = null;
      try { envelope = text ? JSON.parse(text) : null; } catch (_) { /* use generic response */ }
      var value = envelope && envelope.error;
      return fail(
        value && value.code || "HTTP_ERROR",
        value && value.message || "The Agora API request failed with status " + response.status + ".",
        response.status,
        value && value.details
      );
    });
  }

  function requestApi(options, config) {
    var method = options.method;
    var mutating = method !== "GET";
    var tokenPromise = mutating ? Promise.resolve().then(function () {
      if (typeof config.getCsrfToken !== "function") {
        throw fail("CSRF_UNAVAILABLE", "The host application has not supplied a CSRF token provider.");
      }
      return config.getCsrfToken();
    }).then(function (token) {
      if (typeof token !== "string" || !token) throw fail("CSRF_UNAVAILABLE", "The host application has no current CSRF token.");
      return token;
    }) : Promise.resolve("");

    return tokenPromise.then(function (csrfToken) {
      var controller = typeof AbortController === "function" ? new AbortController() : null;
      var timeout = window.setTimeout(function () {
        if (controller) controller.abort();
      }, config.apiTimeoutMs);
      return Promise.resolve().then(function () {
        return config.fetch(buildApiUrl(options.path, config.apiBase), makeFetchInit(
          method, options.body === undefined ? null : options.body, csrfToken, controller && controller.signal
        ));
      }).then(function (response) {
        window.clearTimeout(timeout);
        if (!response.ok) return responseError(response).then(function (error) { throw error; });
        if (options.responseType === "text") return response.text();
        return response.json();
      }).catch(function (error) {
        window.clearTimeout(timeout);
        if (error instanceof AgoraHostError) throw error;
        if (error && error.name === "AbortError") throw fail("TIMEOUT", "The Agora API request timed out.");
        throw fail("FETCH_FAILED", "The Agora API request could not be completed.");
      });
    });
  }

  function buildRequest(method, params, config) {
    var projectPath = "/projects/" + toPathSegment(config.scope.project_id, "projectId");
    var versionPath = projectPath + "/versions/" + toPathSegment(config.scope.version_id, "versionId");
    var recordsPath = projectPath + "/records";

    if (method === "csv") {
      assertKeys(params, [], [], "csv request");
      return { method: "GET", path: versionPath + "/csv", responseType: "text" };
    }
    if (method === "records.list") {
      assertKeys(params, [], [], "records.list request");
      return { method: "GET", path: recordsPath };
    }
    if (method === "records.get") {
      assertKeys(params, ["record_id"], ["record_id"], "records.get request");
      return { method: "GET", path: recordsPath + "/" + toPathSegment(params.record_id, "record_id") };
    }
    if (method === "records.create") {
      assertKeys(params, ["data"], ["data"], "records.create request");
      return { method: "POST", path: recordsPath, body: { data: copyJson(params.data, "data", 1000000) } };
    }
    if (method === "records.update") {
      assertKeys(params, ["record_id", "data", "expected_revision"], ["record_id", "data", "expected_revision"], "records.update request");
      return {
        method: "PUT",
        path: recordsPath + "/" + toPathSegment(params.record_id, "record_id"),
        body: { data: copyJson(params.data, "data", 1000000), expected_revision: assertRevision(params.expected_revision) }
      };
    }
    if (method === "records.delete") {
      assertKeys(params, ["record_id", "expected_revision"], ["record_id", "expected_revision"], "records.delete request");
      return {
        method: "DELETE",
        path: recordsPath + "/" + toPathSegment(params.record_id, "record_id"),
        body: { expected_revision: assertRevision(params.expected_revision) }
      };
    }
    if (method === "sources.list") {
      assertKeys(params, [], [], "sources.list request");
      return { method: "GET", path: projectPath + "/sources" };
    }
    if (method === "sources.catalogs") {
      assertKeys(params, ["source_id"], ["source_id"], "sources.catalogs request");
      var sourceId = toPathSegment(params.source_id, "source_id");
      return { method: "GET", path: projectPath + "/sources/" + sourceId + "/catalogs" };
    }
    if (method === "sources.schemas") {
      assertKeys(params, ["source_id", "catalog"], ["source_id", "catalog"], "sources.schemas request");
      var schemaQuery = new URLSearchParams({ catalog: requireString(params.catalog, "catalog") });
      var schemaSourceId = toPathSegment(params.source_id, "source_id");
      return { method: "GET", path: projectPath + "/sources/" + schemaSourceId + "/schemas?" + schemaQuery.toString() };
    }
    if (method === "sources.tables") {
      assertKeys(params, ["source_id", "catalog", "schema"], ["source_id", "catalog", "schema"], "sources.tables request");
      var tablesSearch = new URLSearchParams({
        catalog: requireString(params.catalog, "catalog"),
        schema: requireString(params.schema, "schema")
      });
      var tablesSourceId = toPathSegment(params.source_id, "source_id");
      return { method: "GET", path: projectPath + "/sources/" + tablesSourceId + "/tables?" + tablesSearch.toString() };
    }
    if (method === "sources.rows") {
      assertKeys(params, ["source_id", "catalog", "schema", "table", "columns", "limit"], ["source_id", "catalog", "schema", "table"], "sources.rows request");
      var rowsBody = {
        catalog: requireString(params.catalog, "catalog"),
        schema: requireString(params.schema, "schema"),
        table: requireString(params.table, "table")
      };
      if (params.columns !== undefined) {
        if (!Array.isArray(params.columns) || params.columns.length > 100) throw fail("INVALID_ARGUMENT", "columns must be an array with at most 100 names.");
        rowsBody.columns = params.columns.map(function (column) { return requireString(column, "column"); });
      }
      if (params.limit !== undefined) {
        if (!Number.isInteger(params.limit) || params.limit < 1 || params.limit > 1000) throw fail("INVALID_ARGUMENT", "limit must be an integer from 1 to 1000.");
        rowsBody.limit = params.limit;
      }
      return {
        method: "POST",
        path: projectPath + "/sources/" + toPathSegment(params.source_id, "source_id") + "/rows",
        body: rowsBody
      };
    }
    throw fail("METHOD_NOT_ALLOWED", "This Agora method is not supported.");
  }

  function makeErrorMessage(error, requestId, frameNonce, scope) {
    return {
      type: "agora:response",
      protocol: PROTOCOL,
      frame_nonce: frameNonce,
      scope: scope,
      request_id: requestId,
      ok: false,
      error: {
        code: error && error.code || "AGORA_ERROR",
        message: error && error.message || "The Agora request failed.",
        details: error && error.details,
        status: error && error.status
      }
    };
  }

  function createAgoraViewerBridge(options) {
    if (!window) throw fail("BROWSER_REQUIRED", "The Agora viewer host adapter requires a browser.");
    options = requireObject(options, "options");
    var iframe = options.iframe;
    if (!iframe || typeof iframe.addEventListener !== "function" || iframe.isConnected !== false || iframe.hasAttribute("src")) {
      throw fail("INVALID_ARGUMENT", "Create an iframe without a src, connect the bridge, then append the iframe.");
    }
    var scope = makeScope(options.projectId, options.versionId, options.capabilities);
    var grantToken = requireString(options.grantToken, "grantToken", 2048);
    var src = requireString(options.src, "src", 4096);
    var targetUrl;
    try { targetUrl = new URL(src, window.location.href); }
    catch (_) { throw fail("INVALID_ARGUMENT", "src must be a valid same-origin viewer URL."); }
    if (targetUrl.origin !== window.location.origin || !/^https?:$/.test(targetUrl.protocol)) {
      throw fail("INVALID_ARGUMENT", "src must use the host application's same origin.");
    }

    var apiBase = normaliseApiBase(options.apiBase);
    var onReady = typeof options.onReady === "function" ? options.onReady : function () {};
    var onError = typeof options.onError === "function" ? options.onError : function () {};
    var fetchFunction = options.fetch || window.fetch;
    if (typeof fetchFunction !== "function") throw fail("FETCH_UNAVAILABLE", "The browser does not support fetch.");
    var config = {
      apiBase: apiBase,
      apiTimeoutMs: options.apiTimeoutMs === undefined ? API_TIMEOUT_MS : Number(options.apiTimeoutMs),
      fetch: fetchFunction.bind ? fetchFunction.bind(window) : fetchFunction,
      getCsrfToken: options.getCsrfToken,
      scope: scope
    };
    if (!Number.isFinite(config.apiTimeoutMs) || config.apiTimeoutMs < 1 || config.apiTimeoutMs > 120000) {
      throw fail("INVALID_ARGUMENT", "apiTimeoutMs must be between 1 and 120000.");
    }

    // Force the isolation contract before the first navigation.
    iframe.setAttribute("sandbox", "allow-scripts");
    iframe.setAttribute("referrerpolicy", "no-referrer");
    iframe.setAttribute("allow", "");

    var state = "waiting-for-load";
    var loadCount = 0;
    var frameNonce = null;
    var port = null;
    var inflight = new Set();
    var lastRequestId = 0;
    var handshakeTimer = null;
    var resolveReady;
    var rejectReady;
    var readySettled = false;
    var ready = new Promise(function (resolve, reject) {
      resolveReady = resolve;
      rejectReady = reject;
    });
    ready.catch(function () {});

    function reportError(error) {
      try { onError(error); } catch (_) { /* consumer callback errors are isolated */ }
    }

    function settleReady(error, value) {
      if (readySettled) return;
      readySettled = true;
      if (error) rejectReady(error);
      else resolveReady(value);
    }

    function closePort() {
      if (port) {
        port.onmessage = null;
        port.onmessageerror = null;
        try { port.close(); } catch (_) { /* already closed */ }
        port = null;
      }
      inflight.clear();
    }

    function revoke(error) {
      if (state === "revoked" || state === "destroyed") return;
      state = "revoked";
      if (handshakeTimer !== null) window.clearTimeout(handshakeTimer);
      closePort();
      settleReady(error);
      reportError(error);
    }

    function portMessage(event) {
      var message = event.data;
      if (!message || typeof message !== "object" || message.protocol !== PROTOCOL || message.frame_nonce !== frameNonce) return;
      var messageScope = parseScope(message.scope);
      if (!sameScope(scope, messageScope)) return;

      if (state === "connecting" && message.type === "agora:ready") {
        state = "ready";
        if (handshakeTimer !== null) window.clearTimeout(handshakeTimer);
        port.postMessage({
          type: "agora:connected",
          protocol: PROTOCOL,
          frame_nonce: frameNonce,
          scope: scope
        });
        var readyInfo = Object.freeze({
          projectId: scope.project_id,
          versionId: scope.version_id,
          capabilities: scope.capabilities.slice()
        });
        settleReady(null, readyInfo);
        try { onReady(readyInfo); } catch (_) { /* consumer callback errors are isolated */ }
        return;
      }

      if (state !== "ready" || message.type !== "agora:request" ||
          typeof message.request_id !== "string" || message.request_id.length > 128 ||
          typeof message.method !== "string" || !isPlainObject(message.params)) return;

      var requestId = message.request_id;
      if (!/^[1-9][0-9]{0,15}$/.test(requestId)) return;
      var numericRequestId = Number(requestId);
      if (!Number.isSafeInteger(numericRequestId) || numericRequestId <= lastRequestId) return;
      lastRequestId = numericRequestId;
      if (inflight.has(requestId)) return;
      if (inflight.size >= MAX_INFLIGHT) {
        safePost(makeErrorMessage(fail("TOO_MANY_REQUESTS", "The viewer has too many active requests.", 429), requestId, frameNonce, scope));
        return;
      }
      if (message.scope.project_id !== scope.project_id || message.scope.version_id !== scope.version_id ||
          !sameScope(scope, messageScope)) return;

      var capability = METHOD_CAPABILITY[message.method];
      if (!capability) {
        safePost(makeErrorMessage(fail("METHOD_NOT_ALLOWED", "This Agora method is not supported.", 400), requestId, frameNonce, scope));
        return;
      }
      if (scope.capabilities.indexOf(capability) < 0) {
        safePost(makeErrorMessage(fail("CAPABILITY_DISABLED", "This project has not enabled that data capability.", 403), requestId, frameNonce, scope));
        return;
      }

      inflight.add(requestId);
      Promise.resolve().then(function () {
        var apiRequest = buildRequest(message.method, message.params, config);
        return requestApi(apiRequest, config);
      }).then(function (result) {
        inflight.delete(requestId);
        safePost({
          type: "agora:response",
          protocol: PROTOCOL,
          frame_nonce: frameNonce,
          scope: scope,
          request_id: requestId,
          ok: true,
          result: result
        });
      }).catch(function (error) {
        inflight.delete(requestId);
        safePost(makeErrorMessage(error, requestId, frameNonce, scope));
      });
    }

    function safePost(message) {
      if (!port || state !== "ready" && message.type !== "agora:connected") return;
      try { port.postMessage(message); }
      catch (_) { revoke(fail("BRIDGE_CLOSED", "The connection to the viewer was interrupted.")); }
    }

    function onPortError() {
      revoke(fail("BRIDGE_CLOSED", "The connection to the viewer was interrupted."));
    }

    function onHostMessage(event) {
      if (event.source !== iframe.contentWindow || event.origin !== "null") return;
      if (state !== "challenged" || event.ports.length !== 0) return;
      var message = event.data;
      if (!message || typeof message !== "object" || message.type !== "agora:hello" ||
          message.protocol !== PROTOCOL || message.frame_nonce !== frameNonce) return;
      if (typeof message.grant_token !== "string" || !constantTimeEqual(message.grant_token, grantToken)) {
        revoke(fail("VIEWER_PROOF_INVALID", "The viewer did not prove it loaded from this project grant."));
        return;
      }

      state = "connecting";
      var channel = new window.MessageChannel();
      port = channel.port1;
      port.onmessage = portMessage;
      port.onmessageerror = onPortError;
      if (typeof port.start === "function") port.start();
      try {
        iframe.contentWindow.postMessage({
          type: "agora:connect",
          protocol: PROTOCOL,
          frame_nonce: frameNonce,
          scope: scope
        }, "*", [channel.port2]);
      } catch (_) {
        try { channel.port2.close(); } catch (ignored) { /* already closed */ }
        revoke(fail("HANDSHAKE_FAILED", "The host could not transfer the viewer connection."));
      }
    }

    function beginFirstLoad() {
      loadCount += 1;
      if (loadCount > 1) {
        revoke(fail("FRAME_NAVIGATED", "The viewer navigated or reloaded. Reopen the project to reconnect."));
        return;
      }
      if (state !== "waiting-for-load") return;
      if (!iframe.sandbox || !iframe.sandbox.contains("allow-scripts") || iframe.sandbox.contains("allow-same-origin")) {
        revoke(fail("ISOLATION_REQUIRED", "The viewer iframe is missing the required sandbox restrictions."));
        return;
      }
      if (typeof window.MessageChannel !== "function") {
        revoke(fail("MESSAGE_CHANNEL_UNAVAILABLE", "This browser does not support isolated viewer connections."));
        return;
      }
      try { frameNonce = randomNonce(); }
      catch (error) { revoke(error); return; }
      state = "challenged";
      handshakeTimer = window.setTimeout(function () {
        revoke(fail("HANDSHAKE_TIMEOUT", "The isolated viewer did not complete its connection."));
      }, HANDSHAKE_TIMEOUT_MS);
      try {
        iframe.contentWindow.postMessage({
          type: "agora:challenge",
          protocol: PROTOCOL,
          frame_nonce: frameNonce
        }, "*");
      } catch (_) {
        revoke(fail("HANDSHAKE_FAILED", "The host could not challenge the isolated viewer."));
      }
    }

    window.addEventListener("message", onHostMessage);
    iframe.addEventListener("load", beginFirstLoad);
    iframe.src = targetUrl.href;

    return Object.freeze({
      ready: ready,
      destroy: function () {
        if (state === "destroyed") return;
        state = "destroyed";
        if (handshakeTimer !== null) window.clearTimeout(handshakeTimer);
        window.removeEventListener("message", onHostMessage);
        iframe.removeEventListener("load", beginFirstLoad);
        closePort();
        settleReady(fail("BRIDGE_DESTROYED", "The viewer connection was closed."));
      }
    });
  }

  return Object.freeze({
    createAgoraViewerBridge: createAgoraViewerBridge,
    AgoraHostError: AgoraHostError,
    protocol: PROTOCOL
  });
});
