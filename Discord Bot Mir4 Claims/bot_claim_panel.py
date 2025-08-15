import os
import discord
from discord.ext import commands, tasks
import logging
import asyncio
from dotenv import load_dotenv
from datetime import datetime, timedelta

# Current UTC time
now_utc = datetime.utcnow()

# Convert to UTC-4
utc_minus_4 = now_utc - timedelta(hours=4)

print("Current time in UTC-4:", utc_minus_4.strftime("%Y-%m-%d %H:%M:%S"))

load_dotenv()
token = os.getenv("DISCORD_TOKEN")

handler = logging.FileHandler(filename='discord.log', encoding='utf-8', mode='w')
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ————————————— Datos y constantes —————————————
active_claims = {}            # {floor: {chamber: {room: {user_id, end_time, tickets}}}}
user_languages = {}           # {user_id: "en" or "es"}

secret_peak_claims = {}      # {floor: {user_id, end_time}}
yellow_boss_timers = {}      # {floor: {"left": end_time, "right": end_time}}
mineral_timers = {}          # {floor: end_time}
user_languages = {}          # {user_id: "en" or "es"}

FLOORS = ["7", "8", "9", "10"]
CHAMBERS = ["Experience Chamber 1", "Experience Chamber 2", "Experience Chamber 3", "Antidemon Chamber"]
ROOMS = ["Left", "Center", "Right"]
TICKET_DURATION = 30 * 60     # segundos por ticket

RED_BOSS_SCHEDULE = [
    (1, "Bottom"), (4, "Top"), (7, "Bottom"), (10, "Top"),
    (13, "Bottom"), (16, "Top"), (19, "Bottom"), (22, "Top")
]

def t(user_id, en, es):
    lang = user_languages.get(user_id, "en")
    return en if lang == "en" else es

def next_red_boss_respawn():
    now_local = datetime.utcnow() - timedelta(hours=4)  # Ajuste a UTC-4
    today = now_local.date()

    for hour, pos in RED_BOSS_SCHEDULE:
        respawn = datetime.combine(today, datetime.min.time()) + timedelta(hours=hour)
        if respawn > now_local:
            return respawn, pos

    # Si ya pasaron todos los horarios de hoy
    tomorrow = today + timedelta(days=1)
    return datetime.combine(tomorrow, datetime.min.time()) + timedelta(hours=1), "Bottom"



# ————————————— Panel Update —————————————
async def update_secret_peak_panel():
    now = asyncio.get_event_loop().time()
    for floor in FLOORS:
        channel_name = f"sp{floor}"
        for guild in bot.guilds:
            channel = discord.utils.get(guild.text_channels, name=channel_name)
            if not channel:
                continue

            embed = discord.Embed(
                title="⛰️ Secret Peak Status",
                description=f"Overview of Floor {floor}",
                color=discord.Color.dark_teal()
            )

            # Floor claim
            claim = secret_peak_claims.get(floor)
            if claim and claim["end_time"] > now:
                user = await bot.fetch_user(claim["user_id"])
                rem = int((claim["end_time"] - now) / 60)
                embed.add_field(name="🔒 Floor Claim", value=f"{user.name} ({rem} min)", inline=False)
            else:
                embed.add_field(name="🔓 Floor Claim", value="Available", inline=False)

            # Yellow bosses
            left = yellow_boss_timers.get(floor, {}).get("left", 0)
            right = yellow_boss_timers.get(floor, {}).get("right", 0)
            embed.add_field(name="🟡 Yellow Boss (Left)", value="Available" if left <= now else f"{int((left - now)/60)} min", inline=True)
            embed.add_field(name="🟡 Yellow Boss (Right)", value="Available" if right <= now else f"{int((right - now)/60)} min", inline=True)

            # Mineral
            mineral = mineral_timers.get(floor, 0)
            embed.add_field(name="⛏️ Mineral", value="Available" if mineral <= now else f"{int((mineral - now)/60)} min", inline=False)

            # Red boss
            respawn_time, position = next_red_boss_respawn()
            now_utc = datetime.utcnow()
            delta = int((respawn_time - (now_utc - timedelta(hours=4))).total_seconds() / 60)

            embed.add_field(name="🕒 Próximo Boss Rojo", value=f"{respawn_time.strftime('%H:%M')} UTC ({position})", inline=False)
            embed.add_field(name="⏳ Tiempo restante", value=f"{delta} minutos", inline=False)


            # Send or edit
            await clear_channel_except_panel(channel)
            sent = False
            async for msg in channel.history(limit=50):
                if msg.author == bot.user and msg.embeds and msg.embeds[0].title == embed.title:
                    await msg.edit(embed=embed, view=SecretPeakView(floor))
                    sent = True
                    break
            if not sent:
                await channel.send(embed=embed, view=SecretPeakView(floor))

# ————————————— View Components —————————————
class SecretPeakView(discord.ui.View):
    def __init__(self, floor):
        super().__init__(timeout=None)
        self.floor = floor
        self.add_item(LanguageSelect())
        self.add_item(ClaimFloorButton(floor))
        self.add_item(YellowBossButton(floor, "left"))
        self.add_item(YellowBossButton(floor, "right"))
        self.add_item(MineralButton(floor))

class LanguageSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="English", value="en", emoji="🇺🇸"),
            discord.SelectOption(label="Español", value="es", emoji="🇪🇸")
        ]
        super().__init__(placeholder="Choose your language", options=options)

    async def callback(self, interaction: discord.Interaction):
        user_languages[interaction.user.id] = self.values[0]
        await interaction.response.send_message("✅ Language set!", ephemeral=True)

class ClaimFloorButton(discord.ui.Button):
    def __init__(self, floor):
        super().__init__(label="Claim Floor", style=discord.ButtonStyle.primary)
        self.floor = floor

    async def callback(self, interaction):
        now = asyncio.get_event_loop().time()
        if secret_peak_claims.get(self.floor, {}).get("end_time", 0) > now:
            await interaction.response.send_message(t(interaction.user.id, "⛔ Floor is occupied.", "⛔ Piso ocupado."), ephemeral=True)
            return
        secret_peak_claims[self.floor] = {
            "user_id": interaction.user.id,
            "end_time": now + TICKET_DURATION
        }
        await interaction.response.send_message(t(interaction.user.id, "✅ Floor claimed for 30 min.", "✅ Piso reclamado por 30 min."), ephemeral=True)
        await update_secret_peak_panel()

class YellowBossButton(discord.ui.Button):
    def __init__(self, floor, side):
        label = f"Respawn Yellow ({side})"
        super().__init__(label=label, style=discord.ButtonStyle.success)
        self.floor = floor
        self.side = side

    async def callback(self, interaction):
        now = asyncio.get_event_loop().time()
        yellow_boss_timers.setdefault(self.floor, {})[self.side] = now + 3600
        await interaction.response.send_message(t(interaction.user.id, f"✅ Yellow boss ({self.side}) timer started.", f"✅ Timer iniciado para boss amarillo ({self.side})."), ephemeral=True)
        await update_secret_peak_panel()

class MineralButton(discord.ui.Button):
    def __init__(self, floor):
        super().__init__(label="Respawn Mineral", style=discord.ButtonStyle.secondary)
        self.floor = floor

    async def callback(self, interaction):
        now = asyncio.get_event_loop().time()
        mineral_timers[self.floor] = now + 3600
        await interaction.response.send_message(t(interaction.user.id, "✅ Mineral timer started.", "✅ Timer de mineral iniciado."), ephemeral=True)
        await update_secret_peak_panel()

# ————————————— Background Task —————————————
@tasks.loop(seconds=60)
async def update_secret_peak_loop():
    await update_secret_peak_panel()

def t(user_id, en, es):
    lang = user_languages.get(user_id, "en")
    return en if lang == "en" else es

# ————————————— Limpieza y actualización de panel —————————————
async def clear_channel_except_panel(channel):
    panel_msg = None
    async for msg in channel.history(limit=200):
        if msg.author == bot.user and msg.embeds:
            title = msg.embeds[0].title
            if title.startswith("📍 Chamber status") or title.startswith("⛰️ Secret Peak Status"):
                panel_msg = msg
                break
    async for msg in channel.history(limit=200):
        if msg != panel_msg:
            try:
                await msg.delete()
            except:
                pass


async def update_main_panel_per_floor():
    now = asyncio.get_event_loop().time()

    for floor in FLOORS:
        channel_name = f"ms{floor}"
        for guild in bot.guilds:
            channel = discord.utils.get(guild.text_channels, name=channel_name)
            if not channel:
                continue

            # Crear embed base
            embed = discord.Embed(
                title="📍 Chamber status",
                description=f"Current status of the rooms in Floor {floor}.",
                color=discord.Color.dark_gold()
            )

            floor_data = active_claims.get(floor, {})
            for chamber in CHAMBERS:
                chamber_data = floor_data.get(chamber, {})
                text = ""
                for room in ROOMS:
                    claim = chamber_data.get(room)
                    if claim:
                        rem = int((claim["end_time"] - now) / 60)
                        user = await bot.fetch_user(claim["user_id"])
                        text += f"🔴 {room}: {user.name} ({rem} min)\n"
                    else:
                        text += f"🟢 {room}: Available\n"
                embed.add_field(name=f"🧪 {chamber}", value=text, inline=False)

            # Limpiar canal y editar o enviar nuevo panel
            await clear_channel_except_panel(channel)
            sent = False
            async for msg in channel.history(limit=50):
                if msg.author == bot.user and msg.embeds and msg.embeds[0].title == embed.title:
                    await msg.edit(embed=embed)
                    sent = True
                    break
            if not sent:
                await channel.send(embed=embed)

# ————————————— Tarea periódica de expiración —————————————
@tasks.loop(seconds=60)
async def check_expired_claims():
    now = asyncio.get_event_loop().time()
    for floor in list(active_claims):
        for chamber in list(active_claims[floor]):
            for room in list(active_claims[floor][chamber]):
                if active_claims[floor][chamber][room]["end_time"] <= now:
                    active_claims[floor][chamber].pop(room)
    await update_main_panel_per_floor()

# ————————————— Definición de Vistas/Componentes —————————————
class ClaimPanelView(discord.ui.View):
    def __init__(self, floor):
        super().__init__(timeout=None)
        self.add_item(LanguageSelect())
        self.add_item(ChamberSelect(floor))

class LanguageSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="English", value="en", emoji="🇺🇸"),
            discord.SelectOption(label="Español", value="es", emoji="🇪🇸")
        ]
        super().__init__(placeholder="Choose your language", options=options)

    async def callback(self, interaction: discord.Interaction):
        user_languages[interaction.user.id] = self.values[0]
        await interaction.response.send_message("✅ Language set!", ephemeral=True)

class ChamberSelect(discord.ui.Select):
    def __init__(self, floor):
        self.floor = floor
        options = [discord.SelectOption(label=c, value=c) for c in CHAMBERS]
        super().__init__(placeholder="Choose a chamber", options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            t(interaction.user.id, "⚔️ Choose a room", "⚔️ Elige una sala"),
            view=RoomSelectView(interaction.user, self.floor, self.values[0]),
            ephemeral=True
        )

class RoomSelectView(discord.ui.View):
    def __init__(self, user, floor, chamber):
        super().__init__(timeout=None)
        self.add_item(RoomSelect(user, floor, chamber))

class RoomSelect(discord.ui.Select):
    def __init__(self, user, floor, chamber):
        self.user = user
        self.floor = floor
        self.chamber = chamber
        options = [discord.SelectOption(label=r, value=r) for r in ROOMS]
        super().__init__(placeholder="Choose a room", options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            t(interaction.user.id, "🎟️ How many tickets (1–20)?", "🎟️ ¿Cuántos tickets (1–20)?"),
            view=TicketSelectView(self.user, self.floor, self.chamber, self.values[0]),
            ephemeral=True
        )

class TicketSelectView(discord.ui.View):
    def __init__(self, user, floor, chamber, room):
        super().__init__(timeout=None)
        self.add_item(TicketSelect(user, floor, chamber, room))

class TicketSelect(discord.ui.Select):
    def __init__(self, user, floor, chamber, room):
        self.user = user
        self.floor = floor
        self.chamber = chamber
        self.room = room
        options = [discord.SelectOption(label=f"{i} tickets", value=str(i)) for i in range(1, 21)]
        super().__init__(placeholder="Select ticket amount", options=options)

    async def callback(self, interaction: discord.Interaction):
        tickets = int(self.values[0])
        duration = tickets * TICKET_DURATION
        floor_data = active_claims.setdefault(self.floor, {})
        chamber_data = floor_data.setdefault(self.chamber, {})

        if self.room in chamber_data:
            await interaction.response.send_message(t(self.user.id, "⛔ Room is occupied.", "⛔ Sala ocupada."), ephemeral=True)
            return

        end_time = asyncio.get_event_loop().time() + duration
        chamber_data[self.room] = {
            "user_id": self.user.id,
            "end_time": end_time,
            "tickets": tickets
        }
        await interaction.response.send_message(
            t(self.user.id,
              f"✅ Claimed {self.room} in {self.chamber} (Floor {self.floor}) for {tickets} tickets.",
              f"✅ Sala {self.room} en {self.chamber} (Piso {self.floor}) reclamada por {tickets} tickets."),
            view=CancelClaimButton(self.user, self.floor, self.chamber, self.room),
            ephemeral=True
        )
        await update_main_panel_per_floor()

class CancelClaimButton(discord.ui.View):
    def __init__(self, user, floor, chamber, room):
        super().__init__(timeout=None)
        self.user = user
        self.floor = floor
        self.chamber = chamber
        self.room = room

    @discord.ui.button(label="Cancel Claim", style=discord.ButtonStyle.danger, emoji="🛑")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        claim = active_claims.get(self.floor, {}).get(self.chamber, {}).get(self.room)
        if not claim or claim["user_id"] != interaction.user.id:
            await interaction.response.send_message("⛔ You can't cancel this claim.", ephemeral=True)
            return

        active_claims[self.floor][self.chamber].pop(self.room)
        await interaction.response.send_message("✅ Claim cancelled.", ephemeral=True)
        await update_main_panel_per_floor()

# ————————————— Comando de setup —————————————
@bot.command(name="claim")
@commands.has_guild_permissions(administrator=True)
async def setup(ctx):
    """Inicializa el panel interactivo en cada canal ms8–ms11."""
    for floor in FLOORS:
        channel = discord.utils.get(ctx.guild.text_channels, name=f"ms{floor}")
        if not channel:
            await ctx.send(f"❌ Txt channel `ms{floor}` not found.")
            continue

        # Envía embed vacío para que update_main_panel lo actualice
        embed = discord.Embed(
            title="📍 Chamber status",
            description=f"Current status of the rooms in floor {floor}.",
            color=discord.Color.dark_gold()
        )
        await channel.send(embed=embed, view=ClaimPanelView(floor))

    await ctx.send("✅ Panels initialized! Run claims in each `#msX`.")

# ————————————— Eventos de bot —————————————
@bot.event
async def on_ready():
    print(f"{bot.user.name} is online.")
    check_expired_claims.start()
    update_secret_peak_loop.start()
    await update_main_panel_per_floor()
    await update_secret_peak_panel()


bot.run(token, log_handler=handler, log_level=logging.DEBUG)