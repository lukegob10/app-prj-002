(function (window) {
  "use strict";

  if (!window || window.Agora) return;

  var PROTOCOL = "agora.viewer/1";
  var CONNECT_TIMEOUT_MS = 20000;
  var HOST_WAIT_TIMEOUT_MS = 60000;
  var REQUEST_TIMEOUT_MS = 30000;
  var MAX_TIMEOUT_MS = 120000;
  var state = "waiting";
  var challengeNonce = null;
  var frameNonce = null;
  var scope = null;
  var port = null;
  var nextRequestId = 0;
  var pending = new Map();
  var connectTimer = null;
  var resolveReady;
  var rejectReady;

  function AgoraError(code, message, details, status) {
    this.name = "AgoraError";
    this.code = code || "AGORA_ERROR";
    this.message = message || "The Agora request failed.";
    this.details = details;
    this.status = status;
    if (Error.captureStackTrace) Error.captureStackTrace(this, AgoraError);
  }
  AgoraError.prototype = Object.create(Error.prototype);
  AgoraError.prototype.constructor = AgoraError;

  function failReady(error) {
    if (state !== "waiting" && state !== "challenged" && state !== "connecting") return;
    state = "failed";
    if (connectTimer !== null) window.clearTimeout(connectTimer);
    rejectReady(error);
  }

  function failPort(error) {
    if (port) {
      port.onmessage = null;
      port.onmessageerror = null;
      try { port.close(); } catch (_) { /* already closed */ }
      port = null;
    }
    pending.forEach(function (entry) {
      window.clearTimeout(entry.timer);
      entry.reject(error);
    });
    pending.clear();
    failReady(error);
  }

  var ready = new Promise(function (resolve, reject) {
    resolveReady = resolve;
    rejectReady = reject;
  });
  // Keep an unobserved handshake failure from becoming a global rejection.
  ready.catch(function () {});
  if (window.parent === window) {
    window.setTimeout(function () {
      failReady(new AgoraError("HOST_REQUIRED", "Open this dashboard through Agora to use project data."));
    }, 0);
  } else {
    connectTimer = window.setTimeout(function () {
      failReady(new AgoraError("HANDSHAKE_TIMEOUT", "The Agora host did not connect to this dashboard."));
    }, HOST_WAIT_TIMEOUT_MS);
  }

  function readGrantToken() {
    var match = window.location.pathname.match(/\/api\/content\/grants\/([^/]+)\/files(?:\/|$)/);
    if (!match) return "";
    try { return decodeURIComponent(match[1]); }
    catch (_) { return ""; }
  }

  function validNonce(value) {
    return typeof value === "string" && /^[A-Za-z0-9_-]{20,64}$/.test(value);
  }

  function parseScope(value) {
    if (!value || typeof value !== "object" || Array.isArray(value)) return null;
    var keys = Object.keys(value).sort();
    if (keys.join(",") !== "capabilities,project_id,version_id") return null;
    if (typeof value.project_id !== "string" || !value.project_id || value.project_id.length > 200) return null;
    if (typeof value.version_id !== "string" || !value.version_id || value.version_id.length > 200) return null;
    if (!Array.isArray(value.capabilities) || value.capabilities.length > 16) return null;
    var seen = Object.create(null);
    for (var i = 0; i < value.capabilities.length; i += 1) {
      var capability = value.capabilities[i];
      if (typeof capability !== "string" || !/^[a-z][a-z0-9.:-]{0,63}$/.test(capability) || seen[capability]) return null;
      seen[capability] = true;
    }
    return {
      project_id: value.project_id,
      version_id: value.version_id,
      capabilities: value.capabilities.slice().sort()
    };
  }

  function sameScope(left, right) {
    return !!left && !!right && left.project_id === right.project_id &&
      left.version_id === right.version_id &&
      left.capabilities.length === right.capabilities.length &&
      left.capabilities.every(function (capability, index) {
        return capability === right.capabilities[index];
      });
  }

  function parsePortError(value) {
    if (!value || typeof value !== "object") return new AgoraError("INVALID_RESPONSE", "The host returned an invalid response.");
    return new AgoraError(value.code, value.message, value.details, value.status);
  }

  function onPortMessage(event) {
    var message = event.data;
    if (!message || typeof message !== "object" || message.protocol !== PROTOCOL || message.frame_nonce !== frameNonce) return;
    var responseScope = parseScope(message.scope);
    if (!sameScope(scope, responseScope)) return;

    if (message.type === "agora:connected" && state === "connecting") {
      state = "ready";
      if (connectTimer !== null) window.clearTimeout(connectTimer);
      resolveReady(Object.freeze({
        projectId: scope.project_id,
        versionId: scope.version_id,
        capabilities: scope.capabilities.slice()
      }));
      return;
    }

    if (message.type !== "agora:response" || typeof message.request_id !== "string") return;
    var entry = pending.get(message.request_id);
    if (!entry) return;
    pending.delete(message.request_id);
    window.clearTimeout(entry.timer);
    if (message.ok === true) {
      entry.resolve(message.result);
    } else if (message.ok === false) {
      entry.reject(parsePortError(message.error));
    } else {
      entry.reject(new AgoraError("INVALID_RESPONSE", "The host returned an invalid response."));
    }
  }

  function onPortError() {
    failPort(new AgoraError("BRIDGE_CLOSED", "The connection to the Agora host was interrupted."));
  }

  function onParentMessage(event) {
    // The frame has no privileged authority. The parent side checks the opaque
    // origin and the grant proof before it transfers the capability channel.
    if (event.source !== window.parent || event.origin === "null") return;
    var message = event.data;
    if (!message || typeof message !== "object" || message.protocol !== PROTOCOL) return;

    if (message.type === "agora:challenge" && state === "waiting") {
      if (!validNonce(message.frame_nonce)) return;
      challengeNonce = message.frame_nonce;
      state = "challenged";
      if (connectTimer !== null) window.clearTimeout(connectTimer);
      window.parent.postMessage({
        type: "agora:hello",
        protocol: PROTOCOL,
        frame_nonce: challengeNonce,
        grant_token: readGrantToken()
      }, "*");
      connectTimer = window.setTimeout(function () {
        failReady(new AgoraError("HANDSHAKE_TIMEOUT", "The Agora host did not finish connecting."));
      }, CONNECT_TIMEOUT_MS);
      return;
    }

    if (message.type !== "agora:connect" || state !== "challenged" ||
        message.frame_nonce !== challengeNonce || !validNonce(message.frame_nonce) ||
        event.ports.length !== 1) return;

    var receivedScope = parseScope(message.scope);
    if (!receivedScope) return;
    scope = receivedScope;
    frameNonce = challengeNonce;
    port = event.ports[0];
    state = "connecting";
    port.onmessage = onPortMessage;
    port.onmessageerror = onPortError;
    if (typeof port.start === "function") port.start();
    port.postMessage({
      type: "agora:ready",
      protocol: PROTOCOL,
      frame_nonce: frameNonce,
      scope: scope
    });
  }

  window.addEventListener("message", onParentMessage);

  function requireString(value, name) {
    if (typeof value !== "string" || !value.trim() || value.length > 500) {
      throw new AgoraError("INVALID_ARGUMENT", name + " must be a non-empty string.");
    }
    return value;
  }

  function requireObject(value, name) {
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      throw new AgoraError("INVALID_ARGUMENT", name + " must be an object.");
    }
    return value;
  }

  function copyJsonValue(value, name) {
    try {
      var json = JSON.stringify(value);
      if (json === undefined) throw new Error("unsupported value");
      return JSON.parse(json);
    } catch (_) {
      throw new AgoraError("INVALID_ARGUMENT", name + " must contain JSON-compatible data.");
    }
  }

  function expectedRevision(value) {
    if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 1) {
      throw new AgoraError("INVALID_ARGUMENT", "expectedRevision must be a positive integer.");
    }
    return value;
  }

  function call(method, params, timeoutMs) {
    var boundedTimeout = timeoutMs === undefined ? REQUEST_TIMEOUT_MS : Number(timeoutMs);
    if (!Number.isFinite(boundedTimeout) || boundedTimeout < 1 || boundedTimeout > MAX_TIMEOUT_MS) {
      return Promise.reject(new AgoraError("INVALID_ARGUMENT", "timeoutMs must be between 1 and " + MAX_TIMEOUT_MS + "."));
    }
    return ready.then(function () {
      if (state !== "ready" || !port) throw new AgoraError("BRIDGE_CLOSED", "The connection to the Agora host is not available.");
      var capability = METHOD_CAPABILITY[method];
      if (!capability) throw new AgoraError("METHOD_NOT_ALLOWED", "This Agora method is not supported.");
      if (scope.capabilities.indexOf(capability) < 0) {
        throw new AgoraError("CAPABILITY_DISABLED", "This project has not enabled that data capability.");
      }
      var requestId = String(++nextRequestId);
      var cleanParams = copyJsonValue(params, "Request parameters");
      return new Promise(function (resolve, reject) {
        var timer = window.setTimeout(function () {
          pending.delete(requestId);
          reject(new AgoraError("TIMEOUT", "The Agora request timed out."));
        }, boundedTimeout);
        pending.set(requestId, { resolve: resolve, reject: reject, timer: timer });
        try {
          port.postMessage({
            type: "agora:request",
            protocol: PROTOCOL,
            frame_nonce: frameNonce,
            request_id: requestId,
            scope: scope,
            method: method,
            params: cleanParams
          });
        } catch (_) {
          pending.delete(requestId);
          window.clearTimeout(timer);
          reject(new AgoraError("BRIDGE_CLOSED", "The Agora request could not be sent."));
        }
      });
    });
  }

  var METHOD_CAPABILITY = Object.freeze({
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
  });

  function cleanSourceQuery(query) {
    query = requireObject(query, "query");
    var result = {
      source_id: requireString(query.sourceId === undefined ? query.source_id : query.sourceId, "sourceId"),
      catalog: requireString(query.catalog, "catalog"),
      schema: requireString(query.schema, "schema"),
      table: requireString(query.table, "table")
    };
    if (query.columns !== undefined) {
      if (!Array.isArray(query.columns) || query.columns.length > 100) {
        throw new AgoraError("INVALID_ARGUMENT", "columns must be an array with at most 100 names.");
      }
      result.columns = query.columns.map(function (column) { return requireString(column, "column"); });
    }
    if (query.limit !== undefined) {
      if (!Number.isInteger(query.limit) || query.limit < 1 || query.limit > 1000) {
        throw new AgoraError("INVALID_ARGUMENT", "limit must be an integer from 1 to 1000.");
      }
      result.limit = query.limit;
    }
    return result;
  }

  var records = Object.freeze({
    list: function () { return call("records.list", {}); },
    get: function (recordId) { return call("records.get", { record_id: requireString(recordId, "recordId") }); },
    create: function (data) { return call("records.create", { data: copyJsonValue(requireObject(data, "data"), "data") }); },
    update: function (recordId, data, revision) {
      return call("records.update", {
        record_id: requireString(recordId, "recordId"),
        data: copyJsonValue(requireObject(data, "data"), "data"),
        expected_revision: expectedRevision(revision)
      });
    },
    delete: function (recordId, revision) {
      return call("records.delete", {
        record_id: requireString(recordId, "recordId"),
        expected_revision: expectedRevision(revision)
      });
    }
  });

  var sources = Object.freeze({
    list: function () { return call("sources.list", {}); },
    catalogs: function (sourceId) {
      return call("sources.catalogs", { source_id: requireString(sourceId, "sourceId") });
    },
    schemas: function (sourceId, catalog) {
      return call("sources.schemas", {
        source_id: requireString(sourceId, "sourceId"),
        catalog: requireString(catalog, "catalog")
      });
    },
    tables: function (sourceId, catalog, schema) {
      return call("sources.tables", {
        source_id: requireString(sourceId, "sourceId"),
        catalog: requireString(catalog, "catalog"),
        schema: requireString(schema, "schema")
      });
    },
    query: function (query) { return call("sources.rows", cleanSourceQuery(query)); },
    rows: function (query) { return call("sources.rows", cleanSourceQuery(query)); }
  });

  window.Agora = Object.freeze({
    ready: ready,
    csv: function () { return call("csv", {}); },
    records: records,
    sources: sources,
    AgoraError: AgoraError
  });
})(window);
