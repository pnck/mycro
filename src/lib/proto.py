"""Phase 2 message dispatcher: envelope validation + type routing.

Pure Python, transport-agnostic: the raw-TCP task decodes frames via
codec.py, then hands envelopes here. Handlers are synchronous; the
transport owns all coroutine concerns.

ctx is a duck-typed context supplied by the platform layer (code.py on
device, fakes in tests):
    ctx.submit(code) -> (bytecode_len | None, err | None)  compile + start
    ctx.status() -> (state, detail | None)
    ctx.abort() -> bool                                     False = was idle
    ctx.device_info() -> dict
    ctx.runtime                                             Runtime registry

Responses leave ts = 0; the transport stamps it at send time.
"""

from codec import PROTO_VERSION

I32_MIN = -0x80000000
I32_MAX = 0x7FFFFFFF


def response(req, type_, payload=None):
    return {
        "v": PROTO_VERSION,
        "type": type_,
        "id": req.get("id", 0) if isinstance(req, dict) else 0,
        "ts": 0,
        "payload": payload if payload is not None else {},
    }


def error_response(req, code, message):
    eid = 0
    if isinstance(req, dict):
        i = req.get("id")
        if isinstance(i, int) and not isinstance(i, bool):
            eid = i
    return response({"id": eid}, "error", {"code": code, "message": message})


def push(type_, payload=None):
    """Server-initiated message: id is always 0."""
    return {"v": PROTO_VERSION, "type": type_, "id": 0, "ts": 0,
            "payload": payload if payload is not None else {}}


def validate_envelope(env):
    """Returns (code, message) on failure, None when the envelope is well-formed."""
    if env.get("v") != PROTO_VERSION:
        return ("bad_version", "unsupported protocol version: {!r}".format(env.get("v")))
    t = env.get("type")
    if not isinstance(t, str) or not t:
        return ("bad_type", "missing or invalid 'type'")
    i = env.get("id")
    if isinstance(i, bool) or not isinstance(i, int) or not 0 <= i <= 0xFFFFFFFF:
        return ("bad_id", "'id' must be an integer in [0, 2^32)")
    ts = env.get("ts", 0)
    if isinstance(ts, bool) or not isinstance(ts, int):
        return ("bad_ts", "'ts' must be an integer")
    if not isinstance(env.get("payload", {}), dict):
        return ("bad_payload", "'payload' must be an object")
    return None


def _is_i32(v):
    return isinstance(v, int) and not isinstance(v, bool) and I32_MIN <= v <= I32_MAX


# --- standard handlers ---

def _h_submit(payload, ctx, _env):
    code = payload.get("code")
    if not isinstance(code, str) or not code:
        return ("field", "'code' must be a non-empty string")
    if ctx.status()[0] == "running":
        return ("state", "busy: macro is running")
    nbytes, err = ctx.submit(code)
    if err:
        return ("compile_error", err)
    return ("ok", {"bytes": nbytes})


def _h_status(payload, ctx, _env):
    state, detail = ctx.status()
    body = {"state": state}
    if detail:
        body["detail"] = detail
    return ("ok", body)


def _h_abort(payload, ctx, _env):
    if not ctx.abort():
        return ("state", "no macro running")
    return ("ok", {})


def _h_device_info(payload, ctx, _env):
    info = dict(ctx.device_info())
    info["proto"] = PROTO_VERSION
    return ("ok", info)


def _h_event_push(payload, ctx, _env):
    name = payload.get("name")
    fields = payload.get("fields", {})
    if not isinstance(name, str) or not name:
        return ("field", "'name' must be a non-empty string")
    if not isinstance(fields, dict):
        return ("field", "'fields' must be an object of i32 values")
    for k, v in fields.items():
        if not isinstance(k, str) or not _is_i32(v):
            return ("field", "'fields' must map names to i32 values")
    if not ctx.runtime.has_signal(name):
        return ("unknown_signal", "no signal registered: {}".format(name))
    ctx.runtime.fire_signal(name, fields)
    return ("ok", {})


_STANDARD = {
    "macro.submit": _h_submit,
    "macro.status": _h_status,
    "macro.abort": _h_abort,
    "device.info": _h_device_info,
    "event.push": _h_event_push,
}


class Dispatcher:
    def __init__(self, extra=None):
        self._handlers = dict(_STANDARD)
        if extra:
            self._handlers.update(extra)

    def dispatch(self, env, ctx):
        bad = validate_envelope(env)
        if bad:
            code, msg = bad
            return error_response(env, code, msg)
        handler = self._handlers.get(env["type"])
        if handler is None:
            return error_response(env, "unknown_type",
                                  "unknown type: {}".format(env["type"]))
        try:
            outcome = handler(env.get("payload", {}), ctx, env)
        except Exception as e:
            return error_response(env, "internal",
                                  "{}: {}".format(type(e).__name__, e))
        kind, body = outcome
        if kind == "ok":
            return response(env, env["type"] + ".ok", body)
        return error_response(env, kind, body)
