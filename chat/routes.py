"""HTTP surface: POST /api/chat, GET /api/chat/status, GET /health."""
import logging

from flask import Blueprint, Response, current_app, g, jsonify, request
from flask import stream_with_context

from chat.agent import Agent, sse
from chat.auth import require_streamflows_user
from chat.conversation import InvalidHistory, normalise
from chat.security import origin_allowed

log = logging.getLogger(__name__)
chat_bp = Blueprint("chat", __name__)


@chat_bp.route("/health")
def health():
    return jsonify({"status": "ok"})


@chat_bp.route("/api/chat/status")
@require_streamflows_user
def status():
    budget = current_app.config["BUDGET"]
    return jsonify({"authenticated": True, "available": not budget.exhausted()})


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

    agent = Agent(
        corpus=current_app.config["CORPUS"],
        schema_dir=current_app.config["SCHEMA_DIR"],
        client=current_app.config["ANTHROPIC_CLIENT"],
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
            on_usage=lambda usage: budget.settle(estimate, usage),
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
