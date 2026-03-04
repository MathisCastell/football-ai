"""
╔══════════════════════════════════════════════════════════════╗
║  FOOTBALL.AI — BOT DISCORD                                   ║
║                                                              ║
║  1. Installe :                                               ║
║     pip install -r requirements.txt                          ║
║                                                              ║
║  2. Configure .env :                                         ║
║     DISCORD_TOKEN=ton_token_discord                          ║
║     FOOTBALL_API_KEY=ta_cle_api (optionnel)                  ║
║                                                              ║
║  3. Lance :                                                  ║
║     python bot.py                                            ║
╚══════════════════════════════════════════════════════════════╝

Commandes slash (ADMIN uniquement) :
  /panel           — Installe un panneau auto-actualisé dans le salon
  /panel-stop      — Supprime le panneau du salon
  /panel-liste     — Voir tous les panneaux actifs
  /refresh         — Met à jour les données (collecte + prédiction)
  /aide            — Aide et commandes disponibles

Types de panneaux :
  predictions      — Prédictions des prochains matchs d'une ligue
  classement       — Classement d'une ligue
  elo              — Top 20 ELO mondial
  résumé           — Vue d'ensemble (toutes les ligues)
"""

import os
import sqlite3
import json
import asyncio
import sys
from datetime import datetime
from collections import defaultdict

import discord
from discord import app_commands
from discord.ext import commands, tasks

# ─── CONFIG ────────────────────────────────────────────────────

def load_env():
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

load_env()

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", "")
GUILD_ID = os.environ.get("DISCORD_GUILD_ID", "")
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "football.db")
PANELS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "panels.json")

COMPETITIONS = {
    "Premier League": {"emoji": "🏴󠁧󠁢󠁥󠁮󠁧󠁿", "code": "PL", "color": 0x3D195B},
    "Ligue 1": {"emoji": "🇫🇷", "code": "FL1", "color": 0x091C3E},
    "La Liga": {"emoji": "🇪🇸", "code": "PD", "color": 0xFF4B44},
    "Bundesliga": {"emoji": "🇩🇪", "code": "BL1", "color": 0xD20515},
    "Serie A": {"emoji": "🇮🇹", "code": "SA", "color": 0x008C45},
}

PANEL_TYPES = {
    "predictions": "Prédictions des prochains matchs",
    "classement": "Classement de la ligue",
    "elo": "Top 20 ELO mondial",
    "resume": "Vue d'ensemble toutes ligues",
}

# ─── PANELS STORAGE ───────────────────────────────────────────

def load_panels():
    if os.path.exists(PANELS_PATH):
        with open(PANELS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_panels(panels):
    with open(PANELS_PATH, "w", encoding="utf-8") as f:
        json.dump(panels, f, indent=2, ensure_ascii=False)


# ─── HELPERS DB ────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_competitions_in_db():
    try:
        conn = get_db()
        rows = conn.execute("SELECT DISTINCT competition FROM matches").fetchall()
        conn.close()
        return [r["competition"] for r in rows]
    except Exception:
        return []


def get_predictions(competition=None, limit=10):
    conn = get_db()
    if competition:
        rows = conn.execute("""
            SELECT p.prediction_json FROM predictions p
            JOIN matches m ON p.match_id = m.id
            WHERE m.competition = ? AND m.status IN ('SCHEDULED', 'TIMED')
            ORDER BY m.date ASC LIMIT ?
        """, (competition, limit)).fetchall()
    else:
        rows = conn.execute("""
            SELECT p.prediction_json FROM predictions p
            JOIN matches m ON p.match_id = m.id
            WHERE m.status IN ('SCHEDULED', 'TIMED')
            ORDER BY m.date ASC LIMIT ?
        """, (limit,)).fetchall()
    conn.close()
    return [json.loads(r["prediction_json"]) for r in rows]


def get_recent_results(competition=None, limit=10):
    conn = get_db()
    if competition:
        rows = conn.execute("""
            SELECT * FROM matches
            WHERE competition = ? AND status = 'FINISHED'
            ORDER BY date DESC LIMIT ?
        """, (competition, limit)).fetchall()
    else:
        rows = conn.execute("""
            SELECT * FROM matches WHERE status = 'FINISHED'
            ORDER BY date DESC LIMIT ?
        """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_standings(competition):
    conn = get_db()
    matches = [dict(r) for r in conn.execute(
        "SELECT * FROM matches WHERE competition = ? AND status = 'FINISHED'",
        (competition,)
    ).fetchall()]
    conn.close()

    table = defaultdict(lambda: {
        "played": 0, "wins": 0, "draws": 0, "losses": 0,
        "gf": 0, "ga": 0, "points": 0
    })
    for m in matches:
        if m["home_score"] is None:
            continue
        ht, at = m["home_team"], m["away_team"]
        hg, ag = m["home_score"], m["away_score"]
        table[ht]["played"] += 1
        table[ht]["gf"] += hg
        table[ht]["ga"] += ag
        table[at]["played"] += 1
        table[at]["gf"] += ag
        table[at]["ga"] += hg
        if hg > ag:
            table[ht]["wins"] += 1; table[ht]["points"] += 3; table[at]["losses"] += 1
        elif hg < ag:
            table[at]["wins"] += 1; table[at]["points"] += 3; table[ht]["losses"] += 1
        else:
            table[ht]["draws"] += 1; table[ht]["points"] += 1
            table[at]["draws"] += 1; table[at]["points"] += 1

    result = []
    for team, s in table.items():
        gd = s["gf"] - s["ga"]
        result.append({"team": team, **s, "gd": gd})
    result.sort(key=lambda x: (-x["points"], -x["gd"], -x["gf"]))
    return result


def get_team_form(team_name, n=5):
    conn = get_db()
    matches = [dict(r) for r in conn.execute("""
        SELECT * FROM matches
        WHERE (home_team = ? OR away_team = ?) AND status = 'FINISHED'
        ORDER BY date DESC LIMIT ?
    """, (team_name, team_name, n)).fetchall()]
    conn.close()

    results = []
    gf = ga = 0
    for m in matches:
        is_home = m["home_team"] == team_name
        scored = m["home_score"] if is_home else m["away_score"]
        conceded = m["away_score"] if is_home else m["home_score"]
        if scored is None:
            continue
        gf += scored
        ga += conceded
        if scored > conceded:
            results.append("W")
        elif scored == conceded:
            results.append("D")
        else:
            results.append("L")
    return results, gf, ga


def get_elo_rankings(limit=20):
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT team, rating FROM elo_ratings ORDER BY rating DESC LIMIT ?",
            (limit,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        conn.close()
        return []


def search_teams(query):
    conn = get_db()
    rows = conn.execute(
        "SELECT DISTINCT home_team FROM matches WHERE home_team LIKE ? LIMIT 25",
        (f"%{query}%",)
    ).fetchall()
    conn.close()
    return [r["home_team"] for r in rows]


# ─── BOT ──────────────────────────────────────────────────────

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


# ─── VISUAL HELPERS ──────────────────────────────────────────

def form_emoji(letter):
    return {"W": "🟢", "D": "🟡", "L": "🔴"}.get(letter, "⚫")


def confidence_bar(value, length=10):
    filled = round(value / 100 * length)
    return "█" * filled + "░" * (length - filled)


def proba_bar(home, draw, away, length=20):
    h = round(home * length)
    d = round(draw * length)
    a = length - h - d
    return f"🟢{'▓' * h}{'▒' * d}{'░' * a}🔴"


# ─── PANEL EMBED BUILDERS ────────────────────────────────────

def build_panel_predictions(competition):
    """Construit un embed complet avec TOUTES les prédictions d'une ligue."""
    info = COMPETITIONS.get(competition, {"emoji": "⚽", "color": 0x2B2D31})
    preds = get_predictions(competition=competition, limit=20)

    embed = discord.Embed(
        title=f"{info['emoji']} PRÉDICTIONS — {competition.upper()}",
        color=info["color"],
        timestamp=datetime.now()
    )
    embed.set_author(name="FOOTBALL.AI — Panneau en direct", icon_url="https://i.imgur.com/3ZUrjUP.png")

    if not preds:
        embed.description = "```\n  Aucun match à venir pour le moment.\n  Les données se mettent à jour automatiquement.\n```"
        embed.set_footer(text="Football.AI | Actualisation auto toutes les 60s")
        return [embed]

    embeds = []
    # Grouper par date
    by_date = defaultdict(list)
    for p in preds:
        by_date[p.get("date", "?")].append(p)

    current_embed = embed
    field_count = 0

    for date, matches in by_date.items():
        for p in matches:
            if field_count >= 8:
                current_embed.set_footer(text=f"Football.AI | Actualisation auto toutes les 60s | {datetime.now().strftime('%H:%M:%S')}")
                embeds.append(current_embed)
                current_embed = discord.Embed(
                    title=f"{info['emoji']} PRÉDICTIONS — {competition.upper()} (suite)",
                    color=info["color"],
                    timestamp=datetime.now()
                )
                current_embed.set_author(name="FOOTBALL.AI — Panneau en direct", icon_url="https://i.imgur.com/3ZUrjUP.png")
                field_count = 0

            home = p["home_team"]
            away = p["away_team"]
            hw = p["home_win"]
            dr = p["draw"]
            aw = p["away_win"]
            score = p.get("most_likely_score", "?-?")
            conf = p.get("confidence", 50)

            # Favori
            if hw > aw and hw > dr:
                fav_icon = "🏠"
                fav_name = home[:12]
            elif aw > hw and aw > dr:
                fav_icon = "✈️"
                fav_name = away[:12]
            else:
                fav_icon = "🤝"
                fav_name = "Nul"

            # Forme
            form_h = p.get("form_home", {})
            form_a = p.get("form_away", {})
            fs_h = form_h.get("form_string", "-----")
            fs_a = form_a.get("form_string", "-----")
            form_h_display = "".join(form_emoji(c) for c in fs_h)
            form_a_display = "".join(form_emoji(c) for c in fs_a)

            elo_h = p.get("elo_home", "?")
            elo_a = p.get("elo_away", "?")

            match_text = (
                f"```\n"
                f"📅 {date}\n"
                f"🏠 {hw:.0%}  |  🤝 {dr:.0%}  |  ✈️ {aw:.0%}\n"
                f"```\n"
                f"**Score probable:** `{score}` | **Favori:** {fav_icon} {fav_name}\n"
                f"**ELO:** `{elo_h}` vs `{elo_a}` | **Confiance:** `{conf}%` {confidence_bar(conf, 8)}\n"
                f"**Forme:** {form_h_display} vs {form_a_display}"
            )

            current_embed.add_field(
                name=f"⚽ {home} vs {away}",
                value=match_text,
                inline=False
            )
            field_count += 1

    current_embed.set_footer(text=f"Football.AI | Actualisation auto toutes les 60s | {datetime.now().strftime('%H:%M:%S')}")
    embeds.append(current_embed)
    return embeds


def build_panel_classement(competition):
    """Construit un embed complet du classement d'une ligue."""
    info = COMPETITIONS.get(competition, {"emoji": "⚽", "color": 0x2B2D31})
    standings = get_standings(competition)

    embed = discord.Embed(
        title=f"{info['emoji']} CLASSEMENT — {competition.upper()}",
        color=info["color"],
        timestamp=datetime.now()
    )
    embed.set_author(name="FOOTBALL.AI — Panneau en direct", icon_url="https://i.imgur.com/3ZUrjUP.png")

    if not standings:
        embed.description = "```\n  Aucune donnée disponible.\n  Utilisez /refresh pour mettre à jour.\n```"
        embed.set_footer(text="Football.AI | Actualisation auto toutes les 60s")
        return [embed]

    # Construire le tableau complet
    header = "```\n"
    header += f" {'#':>2}  {'Équipe':<20} {'Pts':>3}  {'MJ':>2}  {'V':>2} {'N':>2} {'D':>2}  {'BP':>3} {'BC':>3} {'Diff':>4}\n"
    header += " " + "─" * 68 + "\n"

    lines_top = []
    lines_bottom = []

    for i, s in enumerate(standings[:20], 1):
        if i == 1:
            rank = "🥇"
        elif i == 2:
            rank = "🥈"
        elif i == 3:
            rank = "🥉"
        elif i <= 4:
            rank = "🔵"  # Champions League
        elif i <= 6:
            rank = "🟠"  # Europa League
        elif i >= 18:
            rank = "🔴"  # Relégation
        else:
            rank = "⚪"

        team = s["team"][:20]
        pts = s["points"]
        played = s["played"]
        gd = s["gd"]
        gd_str = f"+{gd}" if gd > 0 else str(gd)
        w, d, l = s["wins"], s["draws"], s["losses"]

        line = f"{rank} `{i:>2}.` **{team}** — `{pts}pts` | `{played}MJ` | `{w}V {d}N {l}D` | `{gd_str}`"

        if i <= 10:
            lines_top.append(line)
        else:
            lines_bottom.append(line)

    # Légende zones
    legend = "\n🔵 Ligue des Champions | 🟠 Europa League | 🔴 Relégation"

    if lines_top:
        embed.add_field(
            name="📊 Classement",
            value="\n".join(lines_top),
            inline=False
        )
    if lines_bottom:
        embed.add_field(
            name="\u200b",  # Séparateur invisible
            value="\n".join(lines_bottom),
            inline=False
        )

    embed.add_field(name="\u200b", value=legend, inline=False)

    # Derniers résultats
    recent = get_recent_results(competition=competition, limit=5)
    if recent:
        results_lines = []
        for m in recent:
            hs = m.get("home_score", "?")
            aws = m.get("away_score", "?")
            results_lines.append(f"`{m['date'][:10]}` {m['home_team'][:15]} **{hs}-{aws}** {m['away_team'][:15]}")
        embed.add_field(
            name="📋 Derniers résultats",
            value="\n".join(results_lines),
            inline=False
        )

    embed.set_footer(text=f"Football.AI | {len(standings)} équipes | Actualisation auto | {datetime.now().strftime('%H:%M:%S')}")
    return [embed]


def build_panel_elo():
    """Construit un embed du classement ELO mondial."""
    rankings = get_elo_rankings(20)

    embed = discord.Embed(
        title="🏆 CLASSEMENT ELO MONDIAL",
        color=0xFFD700,
        timestamp=datetime.now()
    )
    embed.set_author(name="FOOTBALL.AI — Panneau en direct", icon_url="https://i.imgur.com/3ZUrjUP.png")

    if not rankings:
        embed.description = "```\n  Aucune donnée ELO disponible.\n  Utilisez /refresh pour calculer.\n```"
        embed.set_footer(text="Football.AI | Actualisation auto toutes les 60s")
        return [embed]

    max_elo = rankings[0]["rating"]
    lines = []
    for i, r in enumerate(rankings[:20], 1):
        if i == 1:
            medal = "🥇"
        elif i == 2:
            medal = "🥈"
        elif i == 3:
            medal = "🥉"
        else:
            medal = f"`{i:>2}.`"

        bar_len = round((r["rating"] / max_elo) * 15)
        bar = "█" * bar_len + "░" * (15 - bar_len)
        diff = r["rating"] - 1500
        diff_str = f"+{diff}" if diff > 0 else str(diff)
        lines.append(f"{medal} **{r['team'][:18]}** `{r['rating']}` ({diff_str}) `{bar}`")

    embed.description = "\n".join(lines)
    embed.set_footer(text=f"Football.AI | {len(rankings)} équipes | Actualisation auto | {datetime.now().strftime('%H:%M:%S')}")
    return [embed]


def build_panel_resume():
    """Construit un résumé global de toutes les ligues."""
    embed = discord.Embed(
        title="⚽ FOOTBALL.AI — VUE D'ENSEMBLE",
        description="Résumé en direct de toutes les compétitions",
        color=0x2B2D31,
        timestamp=datetime.now()
    )
    embed.set_author(name="FOOTBALL.AI — Panneau en direct", icon_url="https://i.imgur.com/3ZUrjUP.png")

    available = get_competitions_in_db()

    for comp_name, info in COMPETITIONS.items():
        if comp_name not in available:
            continue

        # Leader du classement
        standings = get_standings(comp_name)
        leader = standings[0] if standings else None

        # Prochain match
        preds = get_predictions(competition=comp_name, limit=3)

        section = ""
        if leader:
            section += f"🥇 **{leader['team']}** — `{leader['points']}pts` ({leader['wins']}V {leader['draws']}N {leader['losses']}D)\n"

        if preds:
            section += "\n**Prochains matchs:**\n"
            for p in preds[:3]:
                hw = p["home_win"]
                aw = p["away_win"]
                score = p.get("most_likely_score", "?-?")
                conf = p.get("confidence", 50)
                section += f"> `{p.get('date', '?')[:10]}` {p['home_team'][:14]} vs {p['away_team'][:14]} → `{score}` ({conf}%)\n"
        else:
            section += "*Pas de matchs à venir*\n"

        embed.add_field(
            name=f"{info['emoji']} {comp_name}",
            value=section,
            inline=False
        )

    # Stats globales
    try:
        conn = get_db()
        total_matches = conn.execute("SELECT COUNT(*) as c FROM matches WHERE status = 'FINISHED'").fetchone()["c"]
        total_upcoming = conn.execute("SELECT COUNT(*) as c FROM matches WHERE status IN ('SCHEDULED', 'TIMED')").fetchone()["c"]
        total_goals = conn.execute("SELECT COALESCE(SUM(home_score + away_score), 0) as g FROM matches WHERE status = 'FINISHED'").fetchone()["g"]
        conn.close()

        embed.add_field(
            name="📊 Statistiques globales",
            value=(
                f"**{total_matches}** matchs joués | **{total_upcoming}** à venir\n"
                f"**{total_goals}** buts marqués | **{total_goals / max(total_matches, 1):.1f}** buts/match"
            ),
            inline=False
        )
    except Exception:
        pass

    embed.set_footer(text=f"Football.AI | Actualisation auto toutes les 60s | {datetime.now().strftime('%H:%M:%S')}")
    return [embed]


def build_panel_embeds(panel_type, competition=None):
    """Dispatcher pour construire les embeds d'un panneau."""
    if panel_type == "predictions":
        return build_panel_predictions(competition or "Premier League")
    elif panel_type == "classement":
        return build_panel_classement(competition or "Premier League")
    elif panel_type == "elo":
        return build_panel_elo()
    elif panel_type == "resume":
        return build_panel_resume()
    return []


# ─── BACKGROUND TASK ─────────────────────────────────────────

@tasks.loop(seconds=60)
async def update_panels():
    """Met à jour tous les panneaux toutes les 60 secondes."""
    panels = load_panels()
    if not panels:
        return

    to_remove = []

    for channel_id_str, panel_info in panels.items():
        channel_id = int(channel_id_str)
        try:
            channel = bot.get_channel(channel_id)
            if channel is None:
                channel = await bot.fetch_channel(channel_id)

            if channel is None:
                to_remove.append(channel_id_str)
                continue

            panel_type = panel_info["type"]
            competition = panel_info.get("competition")
            message_ids = panel_info.get("message_ids", [])

            # Construire les nouveaux embeds
            embeds = build_panel_embeds(panel_type, competition)
            if not embeds:
                continue

            # Mettre à jour ou recréer les messages
            updated_ids = []
            for idx, emb in enumerate(embeds):
                if idx < len(message_ids):
                    # Essayer d'éditer le message existant
                    try:
                        msg = await channel.fetch_message(message_ids[idx])
                        await msg.edit(content=None, embed=emb)
                        updated_ids.append(message_ids[idx])
                    except (discord.NotFound, discord.HTTPException):
                        # Le message a été supprimé, en créer un nouveau
                        msg = await channel.send(embed=emb)
                        updated_ids.append(msg.id)
                else:
                    # Nouveau message nécessaire
                    msg = await channel.send(embed=emb)
                    updated_ids.append(msg.id)

            # Supprimer les messages en trop (si moins d'embeds qu'avant)
            for old_id in message_ids[len(embeds):]:
                try:
                    old_msg = await channel.fetch_message(old_id)
                    await old_msg.delete()
                except (discord.NotFound, discord.HTTPException):
                    pass

            # Sauvegarder les IDs mis à jour
            panel_info["message_ids"] = updated_ids

        except discord.Forbidden:
            print(f"⚠️ Pas de permission pour le salon {channel_id}")
            to_remove.append(channel_id_str)
        except Exception as e:
            print(f"⚠️ Erreur mise à jour panneau {channel_id}: {e}")

    # Nettoyer les panneaux invalides
    for cid in to_remove:
        del panels[cid]

    save_panels(panels)


@update_panels.before_loop
async def before_update_panels():
    await bot.wait_until_ready()


# ─── COMMANDS ─────────────────────────────────────────────────

@bot.event
async def on_ready():
    print(f"✅ Bot connecté : {bot.user}")
    try:
        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            bot.tree.copy_global_to(guild=guild)
            synced = await bot.tree.sync(guild=guild)
            print(f"✅ {len(synced)} commandes synchronisées sur le serveur {GUILD_ID}")
        else:
            synced = await bot.tree.sync()
            print(f"✅ {len(synced)} commandes synchronisées (global, peut prendre ~1h)")
            print("   💡 Ajoute DISCORD_GUILD_ID dans .env pour une sync instantanée")
    except Exception as e:
        print(f"❌ Erreur sync: {e}")

    # Démarrer la boucle de mise à jour des panneaux
    if not update_panels.is_running():
        update_panels.start()
        print("🔄 Panneaux en direct activés (actualisation toutes les 60s)")

    # Charger les panneaux existants
    panels = load_panels()
    if panels:
        print(f"📋 {len(panels)} panneau(x) actif(s)")


# ─── /panel — Installer un panneau ───────────────────────────

@bot.tree.command(name="panel", description="Installe un panneau auto-actualisé dans ce salon (Admin)")
@app_commands.describe(
    type="Type de panneau à afficher",
    ligue="Ligue (pour prédictions et classement)"
)
@app_commands.choices(type=[
    app_commands.Choice(name="📊 Prédictions des matchs", value="predictions"),
    app_commands.Choice(name="🏆 Classement de la ligue", value="classement"),
    app_commands.Choice(name="⚡ Top 20 ELO mondial", value="elo"),
    app_commands.Choice(name="🌍 Résumé toutes ligues", value="resume"),
])
@app_commands.choices(ligue=[
    app_commands.Choice(name="🏴󠁧󠁢󠁥󠁮󠁧󠁿 Premier League", value="Premier League"),
    app_commands.Choice(name="🇫🇷 Ligue 1", value="Ligue 1"),
    app_commands.Choice(name="🇪🇸 La Liga", value="La Liga"),
    app_commands.Choice(name="🇩🇪 Bundesliga", value="Bundesliga"),
    app_commands.Choice(name="🇮🇹 Serie A", value="Serie A"),
])
@app_commands.default_permissions(administrator=True)
async def cmd_panel(interaction: discord.Interaction, type: str, ligue: str = None):
    await interaction.response.defer(ephemeral=True)

    # Vérifier que c'est un type valide
    if type not in PANEL_TYPES:
        await interaction.followup.send("❌ Type de panneau invalide.", ephemeral=True)
        return

    # Vérifier si une ligue est nécessaire
    if type in ("predictions", "classement") and not ligue:
        await interaction.followup.send(
            "❌ Tu dois choisir une **ligue** pour ce type de panneau.\n"
            "Exemple: `/panel type:predictions ligue:Ligue 1`",
            ephemeral=True
        )
        return

    channel = interaction.channel
    channel_id = str(channel.id)

    # Supprimer l'ancien panneau s'il existe
    panels = load_panels()
    if channel_id in panels:
        old_ids = panels[channel_id].get("message_ids", [])
        for mid in old_ids:
            try:
                msg = await channel.fetch_message(mid)
                await msg.delete()
            except (discord.NotFound, discord.HTTPException):
                pass

    # Construire et envoyer les embeds
    embeds = build_panel_embeds(type, ligue)
    if not embeds:
        await interaction.followup.send("❌ Aucune donnée disponible. Lance `/refresh` d'abord.", ephemeral=True)
        return

    message_ids = []
    for emb in embeds:
        msg = await channel.send(embed=emb)
        message_ids.append(msg.id)

    # Sauvegarder le panneau
    panels[channel_id] = {
        "type": type,
        "competition": ligue,
        "message_ids": message_ids,
        "guild_id": str(interaction.guild_id),
        "created_by": str(interaction.user.id),
        "created_at": datetime.now().isoformat()
    }
    save_panels(panels)

    type_name = PANEL_TYPES[type]
    ligue_info = f" — **{ligue}**" if ligue else ""
    await interaction.followup.send(
        f"✅ Panneau **{type_name}**{ligue_info} installé dans <#{channel.id}> !\n"
        f"Il se mettra à jour automatiquement toutes les **60 secondes**.\n"
        f"Utilise `/panel-stop` pour le supprimer.",
        ephemeral=True
    )


# ─── /panel-stop — Supprimer un panneau ──────────────────────

@bot.tree.command(name="panel-stop", description="Supprime le panneau de ce salon (Admin)")
@app_commands.default_permissions(administrator=True)
async def cmd_panel_stop(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    channel_id = str(interaction.channel_id)
    panels = load_panels()

    if channel_id not in panels:
        await interaction.followup.send("❌ Aucun panneau actif dans ce salon.", ephemeral=True)
        return

    # Supprimer les messages
    old_ids = panels[channel_id].get("message_ids", [])
    for mid in old_ids:
        try:
            msg = await interaction.channel.fetch_message(mid)
            await msg.delete()
        except (discord.NotFound, discord.HTTPException):
            pass

    del panels[channel_id]
    save_panels(panels)

    await interaction.followup.send("✅ Panneau supprimé de ce salon.", ephemeral=True)


# ─── /panel-liste — Voir les panneaux actifs ─────────────────

@bot.tree.command(name="panel-liste", description="Voir tous les panneaux actifs (Admin)")
@app_commands.default_permissions(administrator=True)
async def cmd_panel_liste(interaction: discord.Interaction):
    panels = load_panels()

    if not panels:
        await interaction.response.send_message(
            embed=discord.Embed(
                title="📋 Panneaux actifs",
                description="Aucun panneau actif.\nUtilise `/panel` pour en créer un !",
                color=0x2B2D31
            ),
            ephemeral=True
        )
        return

    embed = discord.Embed(
        title="📋 Panneaux actifs",
        color=0x00E5A0,
        timestamp=datetime.now()
    )

    for cid, info in panels.items():
        ptype = info["type"]
        comp = info.get("competition", "—")
        created = info.get("created_at", "?")[:16]
        n_msgs = len(info.get("message_ids", []))

        embed.add_field(
            name=f"<#{cid}>",
            value=(
                f"**Type:** {PANEL_TYPES.get(ptype, ptype)}\n"
                f"**Ligue:** {comp}\n"
                f"**Messages:** {n_msgs}\n"
                f"**Créé:** {created}"
            ),
            inline=True
        )

    embed.set_footer(text=f"Football.AI | {len(panels)} panneau(x) | Actualisation toutes les 60s")
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ─── /refresh — Mise à jour des données ──────────────────────

@bot.tree.command(name="refresh", description="Met à jour les données (collecte + prédictions) (Admin)")
@app_commands.default_permissions(administrator=True)
async def cmd_refresh(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    embed = discord.Embed(
        title="🔄 Mise à jour en cours...",
        description="Collecte des données et calcul des prédictions.\nCela peut prendre 1-2 minutes.",
        color=0xF5C542
    )
    msg = await interaction.followup.send(embed=embed, ephemeral=True)

    scripts = ["1_collect_data.py", "2_predict.py", "3_export_json.py"]
    base_dir = os.path.dirname(os.path.abspath(__file__))
    results = []

    for script in scripts:
        script_path = os.path.join(base_dir, script)
        if not os.path.exists(script_path):
            results.append(f"⚠️ `{script}` non trouvé")
            continue
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, script_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=base_dir
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
            if proc.returncode == 0:
                results.append(f"✅ `{script}` OK")
            else:
                err = stderr.decode()[:100]
                results.append(f"❌ `{script}` erreur: {err}")
        except asyncio.TimeoutError:
            results.append(f"⏰ `{script}` timeout (>2min)")
        except Exception as e:
            results.append(f"❌ `{script}`: {e}")

    embed = discord.Embed(
        title="✅ Mise à jour terminée",
        description="\n".join(results) + "\n\n📺 Les panneaux se mettront à jour au prochain cycle (< 60s).",
        color=0x00E5A0,
        timestamp=datetime.now()
    )
    embed.set_footer(text="Football.AI")
    await msg.edit(embed=embed)


# ─── /aide — Aide ────────────────────────────────────────────

@bot.tree.command(name="aide", description="Aide et commandes disponibles")
async def cmd_aide(interaction: discord.Interaction):
    embed = discord.Embed(
        title="⚽ Football.AI — Aide",
        description="Bot de prédictions de matchs de football avec panneaux en direct.",
        color=0x2B2D31
    )
    embed.add_field(
        name="📺 Panneaux automatiques (Admin)",
        value=(
            "`/panel` — Installe un panneau auto-actualisé dans le salon\n"
            "`/panel-stop` — Supprime le panneau du salon\n"
            "`/panel-liste` — Voir tous les panneaux actifs\n"
            "`/refresh` — Mettre à jour les données"
        ),
        inline=False
    )
    embed.add_field(
        name="📺 Types de panneaux",
        value=(
            "**predictions** — Tous les matchs à venir avec prédictions\n"
            "**classement** — Classement complet de la ligue\n"
            "**elo** — Top 20 mondial par ELO rating\n"
            "**resume** — Vue d'ensemble de toutes les ligues"
        ),
        inline=False
    )
    embed.add_field(
        name="💡 Comment ça marche ?",
        value=(
            "1. Un admin fait `/panel` dans un salon\n"
            "2. Le bot poste un message complet\n"
            "3. Ce message se met à jour **toutes les 60 secondes**\n"
            "4. Les membres n'ont **rien à faire** — tout est automatique !"
        ),
        inline=False
    )
    embed.add_field(
        name="🧠 Modèles utilisés",
        value=(
            "**Poisson** — Modélisation des buts\n"
            "**ELO** — Force relative des équipes\n"
            "**Forme** — 5 derniers matchs pondérés\n"
            "**H2H** — Confrontations directes"
        ),
        inline=False
    )
    embed.add_field(
        name="🏟️ Ligues disponibles",
        value=" ".join(f"{v['emoji']} {k}" for k, v in COMPETITIONS.items()),
        inline=False
    )
    embed.set_footer(text="Football.AI | Développé avec ❤️")
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ─── RUN ──────────────────────────────────────────────────────

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("❌ DISCORD_TOKEN manquant !")
        print("   Ajoute-le dans ton fichier .env :")
        print("   DISCORD_TOKEN=ton_token_ici")
        print()
        print("   Pour créer un bot Discord :")
        print("   1. Va sur https://discord.com/developers/applications")
        print("   2. Crée une application → Bot → copie le token")
        print("   3. Active les Privileged Gateway Intents")
        print("   4. Invite le bot avec le scope 'bot' + 'applications.commands'")
        sys.exit(1)

    print("🚀 Démarrage du bot Football.AI...")
    bot.run(DISCORD_TOKEN)
