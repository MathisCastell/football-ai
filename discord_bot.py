import discord
from discord.ext import commands, tasks
import requests
import json
from datetime import datetime

# ══ CONFIG ══════════════════════════════════════════
BOT_TOKEN = "MTQ3ODc0MTk0MzgyNjc4MDM4MQ.GCiSA2.yrt4iLzBU3T-WfnqHIP-uydOKZ87lzyQBNZ4T4"
CHANNEL_ID = 123456789  # L'ID du salon Discord (voir étape 5)
API_URL = "http://localhost:5000/data"  # Ton serveur Football.AI
# ════════════════════════════════════════════════════

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"✅ Bot connecté : {bot.user}")
    envoyer_predictions.start()

@tasks.loop(hours=6)
async def envoyer_predictions():
    channel = bot.get_channel(CHANNEL_ID)
    if not channel:
        return

    try:
        resp = requests.get(API_URL, timeout=10)
        data = resp.json()
    except Exception as e:
        await channel.send(f"❌ Erreur serveur : {e}")
        return

    preds = data.get("predictions", [])
    if not preds:
        await channel.send("⚠️ Aucune prédiction disponible.")
        return

    # Filtre les matchs à haute confiance
    top = [p for p in preds if p.get("confidence", 0) >= 55][:5]

    embed = discord.Embed(
        title="⚽ Prédictions Football.AI du jour",
        color=0x00e5a0,
        timestamp=datetime.utcnow()
    )

    for p in top:
        home = p["home_team"]
        away = p["away_team"]
        conf = p.get("confidence", 0)
        fav = p.get("favorite", "draw")
        hw = round(p.get("home_win", 0) * 100)
        dw = round(p.get("draw", 0) * 100)
        aw = round(p.get("away_win", 0) * 100)

        fav_label = {"home": f"🏠 {home}", "away": f"✈️ {away}", "draw": "🤝 Match nul"}.get(fav, fav)

        embed.add_field(
            name=f"{home} vs {away}",
            value=(
                f"🎯 **Favori** : {fav_label}\n"
                f"📊 `{hw}% | {dw}% | {aw}%`\n"
                f"💡 Confiance : **{conf}%**"
            ),
            inline=False
        )

    embed.set_footer(text="Football.AI • Mis à jour automatiquement")
    await channel.send(embed=embed)

# Commande manuelle : !predictions
@bot.command()
async def predictions(ctx):
    await envoyer_predictions()

bot.run(BOT_TOKEN)