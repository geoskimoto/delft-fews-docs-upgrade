"""HTTP surface: POST /api/chat, GET /api/chat/status, GET /health."""
import logging
import sqlite3
from functools import wraps

from flask import Blueprint, Response, current_app, g, jsonify, request
from flask import stream_with_context

from chat.agent import Agent, sse
from chat.auth import require_admin, require_streamflows_user
from chat.conversation import InvalidHistory, normalise
from chat.identity import storage_key
from chat import config
from chat.limits import BOUNDS, InvalidLimits
from chat.model_choice import InvalidModel
from chat.saved_conversations import InvalidConversation
from chat.security import origin_allowed

log = logging.getLogger(__name__)
chat_bp = Blueprint("chat", __name__)
audit = logging.getLogger("chat.audit")


@chat_bp.route("/health")
def health():
    return jsonify({"status": "ok"})


@chat_bp.route("/api/chat/status")
@require_streamflows_user
def status():
    budget = current_app.config["BUDGET"]
    return jsonify({
        "authenticated": True,
        "available": not budget.exhausted(),
        "is_admin": g.is_admin,
        # Opaque per-user namespace for the browser's conversation store. Never
        # the subject itself — that is an email address and would land in
        # localStorage.
        "storage_key": storage_key(g.current_user),
    })


def _limits_payload():
    limits = current_app.config["LIMITS"].current()
    budget = current_app.config["BUDGET"]
    return {
        "limits": limits,
        "bounds": {k: list(v) for k, v in BOUNDS.items()},
        "spent_usd": budget.limit - budget.remaining(),
        "remaining_usd": budget.remaining(),
    }


@chat_bp.route("/api/chat/admin/limits", methods=["GET"])
@require_admin
def get_limits():
    return jsonify(_limits_payload())


@chat_bp.route("/api/chat/admin/limits", methods=["PUT"])
@require_admin
def put_limits():
    actor = storage_key(g.current_user)
    if not origin_allowed(
        request.headers.get("Origin"), current_app.config["ALLOWED_ORIGIN"]
    ):
        audit.warning("limits change refused: bad origin actor=%s", actor)
        return jsonify({"error": "bad_origin",
                        "message": "This request did not come from the guide."}), 403

    body = request.get_json(silent=True)
    store = current_app.config["LIMITS"]
    try:
        if not isinstance(body, dict):
            raise InvalidLimits("Body must be a JSON object.")
        missing = [k for k in BOUNDS if k not in body]
        if missing:
            raise InvalidLimits("Missing: " + ", ".join(missing))
        old = store.current()
        new = store.save(body["daily_budget_usd"], body["rate_limit_calls"])
    except InvalidLimits as exc:
        audit.warning("limits change rejected actor=%s reason=%s", actor, exc)
        return jsonify({"error": "invalid_limits", "message": str(exc)}), 400

    audit.info(
        "limits changed actor=%s daily_budget_usd %s -> %s, rate_limit_calls %s -> %s",
        actor, old["daily_budget_usd"], new["daily_budget_usd"],
        old["rate_limit_calls"], new["rate_limit_calls"],
    )
    return jsonify(_limits_payload())


def _model_payload():
    return {
        "model": current_app.config["MODEL_STORE"].current(),
        "models": [{"key": k, "label": v["label"]} for k, v in config.MODELS.items()],
    }


@chat_bp.route("/api/chat/admin/model", methods=["GET"])
@require_admin
def get_model():
    return jsonify(_model_payload())


@chat_bp.route("/api/chat/admin/model", methods=["PUT"])
@require_admin
def put_model():
    actor = storage_key(g.current_user)
    if not origin_allowed(
        request.headers.get("Origin"), current_app.config["ALLOWED_ORIGIN"]
    ):
        audit.warning("model change refused: bad origin actor=%s", actor)
        return jsonify({"error": "bad_origin",
                        "message": "This request did not come from the guide."}), 403

    body = request.get_json(silent=True)
    store = current_app.config["MODEL_STORE"]
    try:
        if not isinstance(body, dict) or "model" not in body:
            raise InvalidModel("Body must be a JSON object with a model key.")
        old = store.current()
        new = store.save(body["model"])
    except InvalidModel as exc:
        audit.warning("model change rejected actor=%s reason=%s", actor, exc)
        return jsonify({"error": "invalid_model", "message": str(exc)}), 400

    audit.info("model changed actor=%s model %s -> %s", actor, old, new)
    return jsonify(_model_payload())


def _storage_guarded(view):
    """A storage failure becomes a friendly 503, never a 500 or a stack trace;
    the browser then falls back to its own store and chat keeps working."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        try:
            return view(*args, **kwargs)
        except sqlite3.Error:
            log.exception("saved conversation storage failed")
            return jsonify({
                "error": "storage_unavailable",
                "message": "Saved conversations are unavailable right now. "
                           "Your chat still works.",
            }), 503
    return wrapped


def _origin_refused():
    if origin_allowed(
        request.headers.get("Origin"), current_app.config["ALLOWED_ORIGIN"]
    ):
        return None
    return jsonify({"error": "bad_origin",
                    "message": "This request did not come from the guide."}), 403


def _invalid_conversation(message):
    return jsonify({"error": "invalid_conversation", "message": message}), 400


@chat_bp.route("/api/chat/conversations", methods=["GET"])
@require_streamflows_user
@_storage_guarded
def list_conversations():
    owner = storage_key(g.current_user)
    return jsonify({"conversations": current_app.config["CONVERSATIONS"].list(owner)})


@chat_bp.route("/api/chat/conversations/<conv_id>", methods=["GET"])
@require_streamflows_user
@_storage_guarded
def get_conversation(conv_id):
    owner = storage_key(g.current_user)
    found = current_app.config["CONVERSATIONS"].get(owner, conv_id)
    if found is None:
        return jsonify({"error": "not_found"}), 404
    return jsonify({"conversation": found})


@chat_bp.route("/api/chat/conversations/<conv_id>", methods=["PUT"])
@require_streamflows_user
@_storage_guarded
def put_conversation(conv_id):
    if (refused := _origin_refused()):
        return refused
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _invalid_conversation("Body must be a JSON object.")
    owner = storage_key(g.current_user)
    try:
        saved = current_app.config["CONVERSATIONS"].save(
            owner, conv_id, body.get("messages")
        )
    except InvalidConversation as exc:
        return _invalid_conversation(str(exc))
    return jsonify({"conversation": saved})


@chat_bp.route("/api/chat/conversations/<conv_id>", methods=["DELETE"])
@require_streamflows_user
@_storage_guarded
def delete_conversation(conv_id):
    if (refused := _origin_refused()):
        return refused
    current_app.config["CONVERSATIONS"].delete(storage_key(g.current_user), conv_id)
    return jsonify({"ok": True})


@chat_bp.route("/api/chat/conversations", methods=["DELETE"])
@require_streamflows_user
@_storage_guarded
def clear_conversations():
    if (refused := _origin_refused()):
        return refused
    current_app.config["CONVERSATIONS"].clear(storage_key(g.current_user))
    return jsonify({"ok": True})


@chat_bp.route("/api/chat/conversations/import", methods=["POST"])
@require_streamflows_user
@_storage_guarded
def import_conversations():
    if (refused := _origin_refused()):
        return refused
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not isinstance(body.get("conversations"), list):
        return _invalid_conversation("Body must be an object with a conversations array.")
    result = current_app.config["CONVERSATIONS"].import_many(
        storage_key(g.current_user), body["conversations"]
    )
    return jsonify(result)


@chat_bp.route("/api/chat", methods=["POST"])
@require_streamflows_user
def chat():
    if not origin_allowed(
        request.headers.get("Origin"), current_app.config["ALLOWED_ORIGIN"]
    ):
        return jsonify({"error": "bad_origin",
                        "message": "This request did not come from the guide."}), 403

    try:
        messages = normalise(request.get_json(silent=True))
    except InvalidHistory as exc:
        return jsonify({"error": "invalid_history", "message": str(exc)}), 400

    user = g.current_user
    if not current_app.config["RATE_LIMITER"].allow(user):
        return jsonify({
            "error": "rate_limited",
            "message": "That is a lot of questions at once. "
                       "Give it a minute and try again.",
        }), 429

    budget = current_app.config["BUDGET"]
    if budget.exhausted():
        return jsonify({
            "error": "budget_exhausted",
            "message": "The assistant has reached its daily limit and is resting "
                       "until tomorrow. The documentation is still all here.",
        }), 429

    # Read once: a mid-stream admin switch must not change the model or the
    # rates a turn already in flight is priced at.
    model_key = current_app.config["MODEL_STORE"].current()
    agent = Agent(
        corpus=current_app.config["CORPUS"],
        schema_dir=current_app.config["SCHEMA_DIR"],
        client=current_app.config["ANTHROPIC_CLIENT"],
        model_key=model_key,
    )

    # Reserve the worst case for each call BEFORE it is dispatched, then settle
    # to the real cost. The earlier shape — check exhausted(), dispatch, record
    # afterwards — is check-then-act: eight threads all read the same pre-spend
    # balance and all pass. Measured at $4.43 spent against a $2.00 ceiling
    # with six concurrent requests.
    estimate = agent.estimated_cost(budget)

    def generate():
        # budget.settle runs per completed API call, not at the end of the
        # stream: a browser that disconnects mid-answer never runs a
        # generator's tail, so end-of-stream accounting would let a client
        # evade the daily ceiling by hanging up every time.
        for frame in agent.run(
            messages,
            on_reserve=lambda: budget.try_reserve(estimate),
            on_usage=lambda usage: budget.settle(estimate, usage, model_key),
        ):
            yield frame
        # No usernames, no message content — counts only.
        log.info("chat turn complete, spent_today=%.4f", budget.limit - budget.remaining())

    response = Response(
        stream_with_context(generate()), mimetype="text/event-stream"
    )
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    return response
