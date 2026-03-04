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

Commandes slash :
  /predictions  — Prédictions des prochains matchs (choix de ligue)
  /classement   — Classement d'une ligue
  /match        — Détail d'un match (stats, prédiction, H2H)
  /forme        — Forme récente d'une équipe
  /elo          — Top ELO ranking
  /refresh      — Met à jour les données (collecte + prédiction)
"""

import os
import sqlite3
import json
import math
import asyncio
import subprocess
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
GUILD_ID = os.environ.get("DISCORD_GUILD_ID", "")  # ID de ton serveur pour sync instantanée
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "football.db")

COMPETITIONS = {
    "Premier League": {"emoji": "🏴󠁧󠁢󠁥󠁮󠁧󠁿", "code": "PL", "color": 0x3D195B},
    "Ligue 1": {"emoji": "🇫🇷", "code": "FL1", "color": 0x091C3E},
    "La Liga": {"emoji": "🇪🇸", "code": "PD", "color": 0xFF4B44},
    "Bundesliga": {"emoji": "🇩🇪", "code": "BL1", "color": 0xD20515},
    "Serie A": {"emoji": "🇮🇹", "code": "SA", "color": 0x008C45},
}

# ─── HELPERS DB ────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_competitions_in_db():
    """Retourne les compétitions disponibles en base."""
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


# ─── LEAGUE SELECT MENU ───────────────────────────────────────

class LeagueSelect(discord.ui.Select):
    def __init__(self, callback_func, placeholder="Choisis une ligue"):
        available = get_competitions_in_db()
        options = []
        for name, info in COMPETITIONS.items():
            if name in available:
                options.append(discord.SelectOption(
                    label=name, emoji=info["emoji"], value=name
                ))
        if not options:
            # Fallback si la DB n'est pas encore remplie
            for name, info in COMPETITIONS.items():
                options.append(discord.SelectOption(
                    label=name, emoji=info["emoji"], value=name
                ))
        super().__init__(placeholder=placeholder, options=options[:25])
        self._callback_func = callback_func

    async def callback(self, interaction: discord.Interaction):
        await self._callback_func(interaction, self.values[0])


class LeagueView(discord.ui.View):
    def __init__(self, callback_func, placeholder="Choisis une ligue"):
        super().__init__(timeout=120)
        self.add_item(LeagueSelect(callback_func, placeholder))


# ─── PAGINATION VIEW ──────────────────────────────────────────

class PaginatedView(discord.ui.View):
    def __init__(self, embeds: list[discord.Embed]):
        super().__init__(timeout=180)
        self.embeds = embeds
        self.page = 0
        self._update_buttons()

    def _update_buttons(self):
        self.prev_btn.disabled = self.page == 0
        self.next_btn.disabled = self.page >= len(self.embeds) - 1

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        self._update_buttons()
        await interaction.response.edit_message(embed=self.embeds[self.page], view=self)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = min(len(self.embeds) - 1, self.page + 1)
        self._update_buttons()
        await interaction.response.edit_message(embed=self.embeds[self.page], view=self)


# ─── EMBEDS BUILDERS ──────────────────────────────────────────

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


def build_prediction_embed(pred, competition=None):
    comp_name = pred.get("competition", competition or "Football")
    info = COMPETITIONS.get(comp_name, {"emoji": "⚽", "color": 0x2B2D31})

    home = pred["home_team"]
    away = pred["away_team"]
    date = pred.get("date", "?")

    hw = pred["home_win"]
    dr = pred["draw"]
    aw = pred["away_win"]

    # Favori
    if hw > aw and hw > dr:
        fav = f"🏠 {home}"
        fav_pct = hw
    elif aw > hw and aw > dr:
        fav = f"✈️ {away}"
        fav_pct = aw
    else:
        fav = "🤝 Match nul"
        fav_pct = dr

    conf = pred.get("confidence", 50)
    score = pred.get("most_likely_score", "?-?")

    embed = discord.Embed(
        title=f"{info['emoji']} {home}  vs  {away}",
        color=info["color"],
        timestamp=datetime.now()
    )
    embed.set_author(name=f"FOOTBALL.AI — {comp_name}", icon_url="https://i.imgur.com/3ZUrjUP.png")

    embed.add_field(name="📅 Date", value=f"`{date}`", inline=True)
    embed.add_field(name="⭐ Favori", value=f"**{fav}**", inline=True)
    embed.add_field(name="🎯 Score probable", value=f"**`{score}`**", inline=True)

    # Probas
    embed.add_field(
        name="📊 Probabilités",
        value=(
            f"```\n"
            f"🏠 Victoire {home[:15]:.<20} {hw:.0%}\n"
            f"🤝 Match nul {'':.<20} {dr:.0%}\n"
            f"✈️  Victoire {away[:15]:.<20} {aw:.0%}\n"
            f"```"
        ),
        inline=False
    )

    # Barre visuelle
    embed.add_field(
        name="📈 Répartition",
        value=f"`{hw:.0%}` {proba_bar(hw, dr, aw)} `{aw:.0%}`",
        inline=False
    )

    # ELO + Confiance
    elo_h = pred.get("elo_home", "?")
    elo_a = pred.get("elo_away", "?")
    embed.add_field(name="🏆 ELO Domicile", value=f"`{elo_h}`", inline=True)
    embed.add_field(name="🏆 ELO Extérieur", value=f"`{elo_a}`", inline=True)
    embed.add_field(
        name="🔒 Confiance",
        value=f"`{conf}%` {confidence_bar(conf)}",
        inline=True
    )

    # Forme
    form_h = pred.get("form_home", {})
    form_a = pred.get("form_away", {})
    fs_h = form_h.get("form_string", "-----")
    fs_a = form_a.get("form_string", "-----")
    form_display_h = " ".join(form_emoji(c) for c in fs_h)
    form_display_a = " ".join(form_emoji(c) for c in fs_a)
    embed.add_field(
        name=f"📋 Forme {home[:20]}",
        value=form_display_h,
        inline=True
    )
    embed.add_field(
        name=f"📋 Forme {away[:20]}",
        value=form_display_a,
        inline=True
    )

    embed.set_footer(text=f"Football.AI | Confiance: {conf}%")
    return embed


def build_standings_embed(competition, standings):
    info = COMPETITIONS.get(competition, {"emoji": "⚽", "color": 0x2B2D31})

    embed = discord.Embed(
        title=f"{info['emoji']} Classement — {competition}",
        color=info["color"],
        timestamp=datetime.now()
    )
    embed.set_author(name="FOOTBALL.AI — Classement", icon_url="https://i.imgur.com/3ZUrjUP.png")

    if not standings:
        embed.description = "Aucune donnée disponible. Lance `/refresh` pour mettre à jour."
        return embed

    lines = []
    for i, s in enumerate(standings[:20], 1):
        # Médailles pour le podium
        if i == 1:
            rank = "🥇"
        elif i == 2:
            rank = "🥈"
        elif i == 3:
            rank = "🥉"
        else:
            rank = f"`{i:>2}.`"

        team = s["team"][:18]
        pts = s["points"]
        played = s["played"]
        gd = s["gd"]
        gd_str = f"+{gd}" if gd > 0 else str(gd)
        w, d, l = s["wins"], s["draws"], s["losses"]

        lines.append(f"{rank} **{team}** — `{pts}pts` | {w}V {d}N {l}D | `{gd_str}`")

    # Split en 2 champs si > 10
    if len(lines) > 10:
        embed.add_field(name="Top 10", value="\n".join(lines[:10]), inline=False)
        embed.add_field(name="11 - 20", value="\n".join(lines[10:]), inline=False)
    else:
        embed.description = "\n".join(lines)

    embed.set_footer(text=f"Football.AI | {len(standings)} équipes | MJ = {standings[0]['played'] if standings else '?'}")
    return embed


def build_form_embed(team_name, results, gf, ga):
    form_display = " ".join(form_emoji(c) for c in results) if results else "Aucune donnée"

    wins = results.count("W")
    draws = results.count("D")
    losses = results.count("L")
    pts = wins * 3 + draws
    max_pts = len(results) * 3

    embed = discord.Embed(
        title=f"📋 Forme — {team_name}",
        color=0x00E5A0 if wins > losses else 0xFF4B6E if losses > wins else 0xF5C542,
        timestamp=datetime.now()
    )
    embed.set_author(name="FOOTBALL.AI — Analyse de forme", icon_url="https://i.imgur.com/3ZUrjUP.png")

    embed.add_field(name="5 derniers matchs", value=form_display, inline=False)
    embed.add_field(name="Bilan", value=f"**{wins}V** {draws}N {losses}D", inline=True)
    embed.add_field(name="Points", value=f"**{pts}** / {max_pts}", inline=True)
    embed.add_field(name="Buts", value=f"⚽ {gf} marqués | {ga} encaissés", inline=False)

    if len(results) > 0:
        pct = pts / max_pts * 100
        embed.add_field(
            name="Performance",
            value=f"`{pct:.0f}%` {confidence_bar(pct)}",
            inline=False
        )

    embed.set_footer(text="Football.AI")
    return embed


def build_elo_embed(rankings):
    embed = discord.Embed(
        title="🏆 Classement ELO",
        color=0xFFD700,
        timestamp=datetime.now()
    )
    embed.set_author(name="FOOTBALL.AI — ELO Rating", icon_url="https://i.imgur.com/3ZUrjUP.png")

    if not rankings:
        embed.description = "Aucune donnée ELO. Lance `/refresh` pour calculer."
        return embed

    lines = []
    max_elo = rankings[0]["rating"]
    for i, r in enumerate(rankings[:20], 1):
        if i == 1:
            medal = "🥇"
        elif i == 2:
            medal = "🥈"
        elif i == 3:
            medal = "🥉"
        else:
            medal = f"`{i:>2}.`"

        bar_len = round((r["rating"] / max_elo) * 10)
        bar = "█" * bar_len + "░" * (10 - bar_len)
        lines.append(f"{medal} **{r['team'][:18]}** `{r['rating']}` {bar}")

    embed.description = "\n".join(lines)
    embed.set_footer(text=f"Football.AI | {len(rankings)} équipes classées")
    return embed


# ─── COMMANDS ─────────────────────────────────────────────────

@bot.event
async def on_ready():
    print(f"✅ Bot connecté : {bot.user}")
    try:
        if GUILD_ID:
            # Sync sur le serveur spécifique = INSTANTANÉ
            guild = discord.Object(id=int(GUILD_ID))
            bot.tree.copy_global_to(guild=guild)
            synced = await bot.tree.sync(guild=guild)
            print(f"✅ {len(synced)} commandes synchronisées sur le serveur {GUILD_ID}")
        else:
            # Sync global = peut prendre jusqu'à 1h
            synced = await bot.tree.sync()
            print(f"✅ {len(synced)} commandes synchronisées (global, peut prendre ~1h)")
            print("   💡 Ajoute DISCORD_GUILD_ID dans .env pour une sync instantanée")
    except Exception as e:
        print(f"❌ Erreur sync: {e}")


@bot.tree.command(name="predictions", description="Prédictions des prochains matchs")
async def cmd_predictions(interaction: discord.Interaction):
    async def on_select(inter: discord.Interaction, league: str):
        await inter.response.defer()
        preds = get_predictions(competition=league, limit=10)
        if not preds:
            await inter.followup.send(
                embed=discord.Embed(
                    title="❌ Aucune prédiction",
                    description=f"Pas de matchs à venir pour **{league}**.\nLance `/refresh` pour mettre à jour.",
                    color=0xFF4B6E
                )
            )
            return

        embeds = [build_prediction_embed(p, league) for p in preds]
        if len(embeds) == 1:
            await inter.followup.send(embed=embeds[0])
        else:
            view = PaginatedView(embeds)
            await inter.followup.send(
                content=f"**{len(embeds)} prédictions** pour {COMPETITIONS.get(league, {}).get('emoji', '⚽')} **{league}** — Utilise ◀ ▶ pour naviguer",
                embed=embeds[0],
                view=view
            )

    await interaction.response.send_message(
        embed=discord.Embed(
            title="⚽ Prédictions — Choisis une ligue",
            description="Sélectionne la compétition dans le menu ci-dessous.",
            color=0x2B2D31
        ),
        view=LeagueView(on_select),
        ephemeral=False
    )


@bot.tree.command(name="classement", description="Classement d'une ligue")
async def cmd_classement(interaction: discord.Interaction):
    async def on_select(inter: discord.Interaction, league: str):
        await inter.response.defer()
        standings = get_standings(league)
        embed = build_standings_embed(league, standings)
        await inter.followup.send(embed=embed)

    await interaction.response.send_message(
        embed=discord.Embed(
            title="📊 Classement — Choisis une ligue",
            description="Sélectionne la compétition dans le menu ci-dessous.",
            color=0x2B2D31
        ),
        view=LeagueView(on_select, "Choisis une ligue"),
        ephemeral=False
    )


@bot.tree.command(name="forme", description="Forme récente d'une équipe")
@app_commands.describe(equipe="Nom de l'équipe (ex: Arsenal, PSG, Real Madrid)")
async def cmd_forme(interaction: discord.Interaction, equipe: str):
    await interaction.response.defer()
    results, gf, ga = get_team_form(equipe)
    if not results:
        # Recherche approximative
        similar = search_teams(equipe)
        if similar:
            desc = "Équipes trouvées :\n" + "\n".join(f"• `{t}`" for t in similar[:10])
        else:
            desc = "Aucune équipe trouvée avec ce nom."
        await interaction.followup.send(
            embed=discord.Embed(title=f"❌ Équipe '{equipe}' non trouvée", description=desc, color=0xFF4B6E)
        )
        return

    embed = build_form_embed(equipe, results, gf, ga)
    await interaction.followup.send(embed=embed)


@cmd_forme.autocomplete("equipe")
async def forme_autocomplete(interaction: discord.Interaction, current: str):
    if len(current) < 2:
        return []
    teams = search_teams(current)
    return [app_commands.Choice(name=t[:100], value=t[:100]) for t in teams[:25]]


@bot.tree.command(name="elo", description="Classement ELO des équipes")
async def cmd_elo(interaction: discord.Interaction):
    await interaction.response.defer()
    rankings = get_elo_rankings(20)
    embed = build_elo_embed(rankings)
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="match", description="Détail complet d'un match à venir")
@app_commands.describe(equipe="Nom d'une des deux équipes")
async def cmd_match(interaction: discord.Interaction, equipe: str):
    await interaction.response.defer()

    conn = get_db()
    rows = conn.execute("""
        SELECT p.prediction_json FROM predictions p
        JOIN matches m ON p.match_id = m.id
        WHERE m.status IN ('SCHEDULED', 'TIMED')
        AND (m.home_team LIKE ? OR m.away_team LIKE ?)
        ORDER BY m.date ASC LIMIT 1
    """, (f"%{equipe}%", f"%{equipe}%")).fetchall()
    conn.close()

    if not rows:
        similar = search_teams(equipe)
        desc = "Équipes trouvées :\n" + "\n".join(f"• `{t}`" for t in similar[:10]) if similar else "Aucune équipe trouvée."
        await interaction.followup.send(
            embed=discord.Embed(title=f"❌ Pas de match à venir pour '{equipe}'", description=desc, color=0xFF4B6E)
        )
        return

    pred = json.loads(rows[0]["prediction_json"])
    embed = build_prediction_embed(pred)

    # Ajouter H2H si disponible
    h2h = pred.get("h2h", {})
    if h2h and h2h.get("total_games", 0) > 0:
        meetings = h2h.get("last_meetings", [])
        h2h_lines = [f"`{m['date']}` {m['home']} **{m['score']}** {m['away']}" for m in meetings[:3]]
        h2h_text = "\n".join(h2h_lines) if h2h_lines else "Aucun"
        embed.add_field(
            name=f"⚔️ Confrontations directes ({h2h['total_games']} matchs)",
            value=f"{h2h.get('home_wins', 0)}V - {h2h.get('draws', 0)}N - {h2h.get('away_wins', 0)}D\n{h2h_text}",
            inline=False
        )

    await interaction.followup.send(embed=embed)


@cmd_match.autocomplete("equipe")
async def match_autocomplete(interaction: discord.Interaction, current: str):
    if len(current) < 2:
        return []
    teams = search_teams(current)
    return [app_commands.Choice(name=t[:100], value=t[:100]) for t in teams[:25]]


@bot.tree.command(name="refresh", description="Met à jour les données (collecte + prédictions)")
async def cmd_refresh(interaction: discord.Interaction):
    await interaction.response.defer()

    embed = discord.Embed(
        title="🔄 Mise à jour en cours...",
        description="Collecte des données et calcul des prédictions.\nCela peut prendre 1-2 minutes.",
        color=0xF5C542
    )
    msg = await interaction.followup.send(embed=embed)

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
        description="\n".join(results),
        color=0x00E5A0,
        timestamp=datetime.now()
    )
    embed.set_footer(text="Football.AI")
    await msg.edit(embed=embed)


@bot.tree.command(name="aide", description="Aide et commandes disponibles")
async def cmd_aide(interaction: discord.Interaction):
    embed = discord.Embed(
        title="⚽ Football.AI — Aide",
        description="Bot de prédictions de matchs de football basé sur l'IA.",
        color=0x2B2D31
    )
    embed.add_field(
        name="📋 Commandes",
        value=(
            "`/predictions` — Prédictions par ligue (avec menu)\n"
            "`/classement` — Classement d'une ligue\n"
            "`/match <équipe>` — Détail d'un match à venir\n"
            "`/forme <équipe>` — Forme récente (5 derniers matchs)\n"
            "`/elo` — Classement ELO de toutes les équipes\n"
            "`/refresh` — Mettre à jour les données\n"
            "`/aide` — Ce message"
        ),
        inline=False
    )
    embed.add_field(
        name="🧠 Modèles utilisés",
        value=(
            "• **Poisson** — Modélisation des buts\n"
            "• **ELO** — Force relative des équipes\n"
            "• **Forme** — 5 derniers matchs pondérés\n"
            "• **H2H** — Confrontations directes"
        ),
        inline=False
    )
    embed.add_field(
        name="🏟️ Ligues disponibles",
        value=" ".join(f"{v['emoji']} {k}" for k, v in COMPETITIONS.items()),
        inline=False
    )
    embed.set_footer(text="Football.AI | Développé avec ❤️")
    await interaction.response.send_message(embed=embed)


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
