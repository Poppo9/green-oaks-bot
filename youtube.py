import discord
from discord.ext import commands
import logging
from dotenv import load_dotenv
import os
import tempfile
import shutil

import yt_dlp

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
COOKIES_PATH = os.getenv("YTDL_COOKIES")  # path al cookies.txt esportato dal browser

handler = logging.FileHandler(filename="discord.log", encoding="utf-8", mode="w")
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Cache locale dei risultati di ricerca
search_cache: dict[int, list[dict]] = {}


def build_ydl_opts() -> dict:
    """Opzioni base per yt‑dlp. Aggiunge i cookie se presenti."""
    opts = {
        "format": "bestaudio/best",
        "quiet": True,
        "no_warnings": True,
        "source_address": "0.0.0.0",
    }
    if COOKIES_PATH:
        opts["cookiefile"] = COOKIES_PATH
    return opts


@bot.event
async def on_ready():
    print(f"SUCCESS: {bot.user.name} avviato correttamente!")


@bot.event
async def on_message(message: discord.Message):
    if message.author == bot.user:
        return

    if "popo" in message.content.lower():
        await message.delete()
        await message.channel.send(f"{message.author.mention} ha detto popo.")

    await bot.process_commands(message)


# -------------------- COMANDI BASE --------------------

@bot.command(name="hello")
async def hello(ctx: commands.Context):
    await ctx.send(f"ciao {ctx.author.mention}")


@bot.command(name="e")
async def estrai_carta(ctx: commands.Context):
    await ctx.send("Hai estratto una carta!")


# -------------------- !yt SEARCH ----------------------

@bot.command(name="yt")
async def yt_search(ctx: commands.Context, *, query: str):
    """Cerca su YouTube e mostra i primi 10 risultati."""

    async with ctx.typing():
        ydl_opts = build_ydl_opts() | {
            "skip_download": True,
            "extract_flat": "in_playlist",
            "default_search": "ytsearch10",
            "forcejson": True,
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                result = ydl.extract_info(query, download=False)
                entries = result.get("entries", [])
        except Exception as e:
            await ctx.send(f"Errore nella ricerca: {e}")
            return

    if not entries:
        await ctx.send("⚠️ Nessun risultato trovato.")
        return

    search_cache[ctx.guild.id] = entries

    elenco = "\n".join(
        f"{idx}. {video.get('title', 'Sconosciuto')[:80]} ({video.get('duration_string', 'LIVE')})"
        for idx, video in enumerate(entries, start=1)
    )

    await ctx.send(
        f"**Risultati per:** `{query}`\n" +
        "```\n" + elenco + "\n```\n" +
        "Digita `!play <numero>` per riprodurre il video nel tuo canale vocale."
    )


# -------------------- !play ---------------------------

@bot.command(name="play")
async def play(ctx: commands.Context, index: int):
    """Scarica l'audio in un file temporaneo e lo riproduce nel canale vocale."""

    if ctx.author.voice is None:
        await ctx.send("❌ Devi essere in un canale vocale prima di usare questo comando.")
        return

    entries = search_cache.get(ctx.guild.id)
    if not entries or not (1 <= index <= len(entries)):
        await ctx.send("⚠️ Indice non valido o nessuna ricerca attiva.")
        return

    video = entries[index - 1]
    title = video.get("title", "Sconosciuto")
    url = video.get("url") or f"https://www.youtube.com/watch?v={video.get('id')}"

    # Connessione / spostamento del bot nel canale vocale
    if ctx.voice_client is None:
        vc = await ctx.author.voice.channel.connect()
    else:
        vc = ctx.voice_client
        if vc.channel != ctx.author.voice.channel:
            await vc.move_to(ctx.author.voice.channel)

    if vc.is_playing():
        vc.stop()

    # Creiamo directory/temp file per il download
    temp_dir = tempfile.mkdtemp(prefix="yt_", dir="/tmp")
    outtmpl = os.path.join(temp_dir, "%(id)s.%(ext)s")

    ydl_opts = build_ydl_opts() | {
        "outtmpl": outtmpl,
        "noplaylist": True,
    }

    # Scarica il file audio
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            audio_path = ydl.prepare_filename(info)
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        await ctx.send(f"Errore durante il download: {e}")
        return

    # Crea la sorgente audio dal file locale (evita ffprobe su HTTPS)
    try:
        source = discord.FFmpegPCMAudio(audio_path)
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        await ctx.send(f"Errore creando sorgente audio: {e}")
        return

    # Callback per pulire il file al termine
    def _after_play(error):
        if error:
            print(f"Player error: {error}")
        shutil.rmtree(temp_dir, ignore_errors=True)

    vc.play(source, after=_after_play)
    await ctx.send(f"▶️ Ora in riproduzione: **{title}**")


# -------------------- !stop ---------------------------

@bot.command(name="stop")
async def stop(ctx: commands.Context):
    if ctx.voice_client is None:
        await ctx.send("Il bot non è collegato a nessun canale vocale.")
        return
    await ctx.voice_client.disconnect()
    await ctx.send("⏹️ Bot disconnesso dal canale vocale.")


# -------------------- MAIN ---------------------------

if __name__ == "__main__":
    bot.run(TOKEN, log_handler=handler, log_level=logging.INFO)
