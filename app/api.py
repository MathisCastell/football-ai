"""Routes HTTP exposées par le service web."""

import json
import os
import threading
from collections import defaultdict
from datetime import datetime

from flask import Blueprint, jsonify, redirect, request, send_from_directory, session

from app import config, pipeline
from app.auth import admin_required, current_user, login_required, premium_required, user_to_dict
from app.db import session_scope
from app.models import (
    EloRating, Match, ModelParams, Prediction, PredictionScore, SupportMessage, User,
)

bp = Blueprint("api", __name__)
_STATIC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _serve(filename):
    return send_from_directory(_STATIC_DIR, filename)


# ── Pages ─────────────────────────────────────────────────────────
# Routes explicites uniquement (pas de wildcard sur le dossier racine du
# projet : servir n'importe quel fichier exposerait .env, football.db, etc.)

@bp.route("/")
def landing():
    return _serve("landing.html")


@bp.route("/login")
def login_page():
    return _serve("login.html")


@bp.route("/app")
def app_page():
    if not session.get("user_id"):
        return redirect("/login?next=/app")
    return _serve("index.html")


@bp.route("/admin")
def admin_page():
    user_id = session.get("user_id")
    if not user_id:
        return redirect("/login?next=/admin")
    with session_scope() as session_:
        user = session_.get(User, user_id)
        if not user or not user.is_admin:
            return redirect("/app")
    return _serve("admin.html")


# ── Compte ────────────────────────────────────────────────────────

@bp.route("/api/me")
def api_me():
    with session_scope() as session_:
        return jsonify({"user": user_to_dict(current_user(session_))})


# ── Support ───────────────────────────────────────────────────────

@bp.route("/api/support", methods=["POST"])
def api_support():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()[:200]
    email = (data.get("email") or "").strip()[:200]
    message = (data.get("message") or "").strip()[:4000]

    if not message or not email:
        return jsonify({"error": "Email et message sont requis."}), 400

    with session_scope() as session_:
        user = current_user(session_)
        session_.add(SupportMessage(
            user_id=user.id if user else None,
            name=name or (user.email if user else ""), email=email, message=message,
            created_at=datetime.now().isoformat(), status="open",
        ))

    return jsonify({"ok": True})


# ── Pipeline ──────────────────────────────────────────────────────

@bp.route("/api/status")
def api_status():
    status = pipeline.get_status()
    return jsonify({
        "status": "running" if status["is_running"] else "idle",
        "last_update": status["last_update"],
        "last_error": status["last_error"],
    })


@bp.route("/api/refresh")
@admin_required
def api_refresh():
    if pipeline.get_status()["is_running"]:
        return jsonify({"ok": False, "message": "Pipeline déjà en cours..."})
    threading.Thread(target=lambda: pipeline.run(force=True), daemon=True).start()
    return jsonify({"ok": True, "message": "Pipeline relancé !"})


# ── Données ───────────────────────────────────────────────────────

def _strip_premium_fields(pred):
    pred = dict(pred)
    for key in ("betting_tips", "advanced_home", "advanced_away"):
        pred.pop(key, None)
    return pred


@bp.route("/api/data")
@login_required
def api_data():
    with session_scope() as session_:
        user = current_user(session_)
        full_access = bool(user and (user.is_premium or user.is_admin))

        all_finished = session_.query(Match).filter(
            Match.status == "FINISHED", Match.home_score.isnot(None)
        ).all()
        if not full_access:
            all_finished = [m for m in all_finished if m.competition in config.FREE_COMPETITIONS]
        finished_sorted = sorted(all_finished, key=lambda m: m.date, reverse=True)

        preds_query = (
            session_.query(Prediction.prediction_json)
            .join(Match, Prediction.match_id == Match.id)
        )
        if not full_access:
            preds_query = preds_query.filter(Match.competition.in_(config.FREE_COMPETITIONS))
        preds_raw = preds_query.order_by(Match.date.asc()).all()
        preds_out = [json.loads(r[0]) for r in preds_raw]
        if not full_access:
            preds_out = [_strip_premium_fields(p) for p in preds_out]

        team_comp = {}
        for m in all_finished:
            team_comp[m.home_team] = m.competition
            team_comp[m.away_team] = m.competition

        elo_query = session_.query(EloRating).order_by(EloRating.rating.desc())
        elo_raw = elo_query.all()
        if not full_access:
            elo_raw = [e for e in elo_raw if e.team in team_comp]

        table = defaultdict(lambda: {"played": 0, "wins": 0, "draws": 0, "losses": 0,
                                      "goals_for": 0, "goals_against": 0, "points": 0})
        for m in all_finished:
            ht, at, hg, ag = m.home_team, m.away_team, m.home_score, m.away_score
            table[ht]["played"] += 1
            table[at]["played"] += 1
            table[ht]["goals_for"] += hg
            table[ht]["goals_against"] += ag
            table[at]["goals_for"] += ag
            table[at]["goals_against"] += hg
            if hg > ag:
                table[ht]["wins"] += 1
                table[ht]["points"] += 3
                table[at]["losses"] += 1
            elif hg < ag:
                table[at]["wins"] += 1
                table[at]["points"] += 3
                table[ht]["losses"] += 1
            else:
                table[ht]["draws"] += 1
                table[ht]["points"] += 1
                table[at]["draws"] += 1
                table[at]["points"] += 1

        standings = sorted(
            [{"team": t, "competition": team_comp.get(t, ""), "goal_diff": s["goals_for"] - s["goals_against"], **s}
             for t, s in table.items()],
            key=lambda x: (x["competition"], -x["points"], -x["goal_diff"], -x["goals_for"])
        )
        current_comp, rank = None, 0
        for t in standings:
            if t["competition"] != current_comp:
                current_comp, rank = t["competition"], 1
            else:
                rank += 1
            t["rank"] = rank

        finished_out = []
        for m in finished_sorted:
            res = "H" if m.home_score > m.away_score else "A" if m.home_score < m.away_score else "D"
            finished_out.append({
                "id": m.id, "date": m.date, "matchday": m.matchday, "competition": m.competition,
                "home_team": m.home_team, "away_team": m.away_team,
                "home_score": m.home_score, "away_score": m.away_score, "result": res,
                "home_xg": m.home_xg, "away_xg": m.away_xg,
                "home_possession": m.home_possession, "away_possession": m.away_possession,
                "home_shots": m.home_shots, "away_shots": m.away_shots,
                "home_shots_on_target": m.home_shots_on_target, "away_shots_on_target": m.away_shots_on_target,
                "home_corners": m.home_corners, "away_corners": m.away_corners,
                "home_fouls": m.home_fouls, "away_fouls": m.away_fouls,
                "home_yellow_cards": m.home_yellow_cards, "away_yellow_cards": m.away_yellow_cards,
                "home_red_cards": m.home_red_cards, "away_red_cards": m.away_red_cards,
                "home_formation": m.home_formation, "away_formation": m.away_formation,
            })

        n = len(all_finished)
        total_goals = sum((m.home_score or 0) + (m.away_score or 0) for m in all_finished)
        status = pipeline.get_status()

        return jsonify({
            "meta": {
                "total_matches_played": n,
                "total_matches_predicted": len(preds_out),
                "total_goals": total_goals,
                "avg_goals_per_match": round(total_goals / n, 2) if n else 0,
                "generated_at": status["last_update"] or datetime.now().isoformat(),
                "season": config.CURRENT_SEASON,
            },
            "access": {
                "full_access": full_access,
                "free_competitions": config.FREE_COMPETITIONS,
            },
            "standings": standings,
            "elo_rankings": [{"team": e.team, "rating": e.rating, "updated_at": e.updated_at} for e in elo_raw],
            "recent_results": finished_out,
            "predictions": preds_out,
        })


@bp.route("/api/model-performance")
@premium_required
def api_model_performance():
    """Transparence sur l'auto-apprentissage : précision réelle du modèle et son
    évolution, pour que ce ne soit pas une boîte noire. Réservé aux comptes premium."""
    with session_scope() as session_:
        scores = session_.query(PredictionScore).order_by(PredictionScore.evaluated_at.asc()).all()
        params = session_.query(ModelParams).all()

        by_month = defaultdict(list)
        for s in scores:
            month = (s.evaluated_at or "")[:7]
            by_month[month].append(s.brier_score)

        brier_trend = [
            {"month": month, "avg_brier_score": round(sum(vals) / len(vals), 4), "n": len(vals)}
            for month, vals in sorted(by_month.items())
        ]

        n_total = len(scores)
        n_correct = sum(1 for s in scores if s.favorite_correct)

        by_competition = defaultdict(list)
        for s in scores:
            by_competition[s.competition].append(s)

        competition_stats = []
        for comp, rows in sorted(by_competition.items()):
            correct = sum(1 for r in rows if r.favorite_correct)
            avg_brier = sum(r.brier_score for r in rows) / len(rows)
            competition_stats.append({
                "competition": comp, "sample_size": len(rows),
                "favorite_hit_rate": round(correct / len(rows), 3) if rows else None,
                "avg_brier_score": round(avg_brier, 4),
            })

        return jsonify({
            "total_predictions_scored": n_total,
            "favorite_hit_rate": round(n_correct / n_total, 3) if n_total else None,
            "brier_trend_by_month": brier_trend,
            "by_competition": competition_stats,
            "model_params": [
                {
                    "competition": p.competition, "k_factor": p.k_factor,
                    "home_advantage_elo": p.home_advantage_elo, "poisson_home_adv": p.poisson_home_adv,
                    "sample_size": p.sample_size, "last_log_loss": p.last_log_loss,
                    "updated_at": p.updated_at,
                }
                for p in params
            ],
        })


# ── Admin ─────────────────────────────────────────────────────────

@bp.route("/api/admin/users")
@admin_required
def api_admin_users():
    with session_scope() as session_:
        users = session_.query(User).order_by(User.created_at.desc()).all()
        return jsonify({"users": [
            {
                "id": u.id, "email": u.email, "is_admin": u.is_admin, "is_premium": u.is_premium,
                "created_at": u.created_at, "last_login_at": u.last_login_at,
            }
            for u in users
        ]})


@bp.route("/api/admin/users/<int:user_id>", methods=["POST"])
@admin_required
def api_admin_update_user(user_id):
    data = request.get_json(silent=True) or {}
    with session_scope() as session_:
        target = session_.get(User, user_id)
        if not target:
            return jsonify({"error": "Utilisateur introuvable."}), 404
        if "is_premium" in data:
            target.is_premium = bool(data["is_premium"])
        if "is_admin" in data:
            # Un admin ne peut pas se retirer lui-même ses droits (évite de se
            # bloquer l'accès à l'espace admin par erreur).
            current = current_user(session_)
            if current and current.id == target.id and not data["is_admin"]:
                return jsonify({"error": "Tu ne peux pas retirer tes propres droits admin."}), 400
            target.is_admin = bool(data["is_admin"])
        return jsonify({"ok": True})


@bp.route("/api/admin/support")
@admin_required
def api_admin_support():
    with session_scope() as session_:
        messages = session_.query(SupportMessage).order_by(SupportMessage.created_at.desc()).all()
        return jsonify({"messages": [
            {
                "id": m.id, "name": m.name, "email": m.email, "message": m.message,
                "created_at": m.created_at, "status": m.status,
            }
            for m in messages
        ]})


@bp.route("/api/admin/support/<int:message_id>", methods=["POST"])
@admin_required
def api_admin_resolve_support(message_id):
    data = request.get_json(silent=True) or {}
    with session_scope() as session_:
        msg = session_.get(SupportMessage, message_id)
        if not msg:
            return jsonify({"error": "Message introuvable."}), 404
        msg.status = data.get("status", "resolved")
        return jsonify({"ok": True})
