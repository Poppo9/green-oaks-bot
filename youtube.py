# -*- coding: utf-8 -*-
"""
Bot musicale Discord.
"""

import os
import shutil
import tempfile
import time
import logging
from collections import deque
from typing import Dict, List, Deque, Optional

import discord
from discord.ext import commands
import yt_dlp
from dotenv import load_dotenv

# ---------------------------------------------------------------------------#
#                      CONFIGURAZIONE E OGGETTI GLOBALI                       #
# ---------------------------------------------------------------------------#
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
COOKIES_PATH = os.getenv("YTDL_COOKIES")

LOG_FILE = "discord.log"
handler = logging.FileHandler(filename=LOG_FILE, encoding="utf-8", mode="w")

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Risultati ricerca video  |  Risultati ricerca playlist
search_cache: Dict[int, List[dict]] = {}
plist_cache: Dict[int, List[dict]] = {}

# Coda e cronologia per server
queue_map: Dict[int, Deque[dict]] = {}
history_map: Dict[int, List[dict]] = {}

TEMP_PARENT = "/tmp"  # Personalizza se vuoi

# ---------------------------------------------------------------------------#
#                               FUNZIONI UTILI                               #
# ---------------------------------------------------------------------------#
def build_ydl_opts(extra: Optional[dict] = None) -> dict:
    """Opzioni base per yt-dlp; include cookies se presenti."""
    opts = {
        "format": "bestaudio/best",
        "quiet": True,
        "no_warnings": True,
        "source_address": "0.0.0.0",
    }
    if COOKIES_PATH:
        opts["cookiefile"] = COOKIES_PATH
    if extra:
        opts.update(extra)
    return opts


async def ensure_voice(ctx: commands.Context) -> Optional[discord.VoiceClient]:
    """Collega oppure sposta il bot nel canale vocale dell’utente."""
    if ctx.author.voice is None:
        await ctx.send("❌ Devi essere in un canale vocale.")
        return None

    if ctx.voice_client is None:
        return await ctx.author.voice.channel.connect()

    if ctx.voice_client.channel != ctx.author.voice.channel:
        await ctx.voice_client.move_to(ctx.author.voice.channel)
    return ctx.voice_client


def add_song(guild_id: int, video: dict):
    """Aggiunge un brano alla coda del server."""
    queue_map.setdefault(guild_id, deque()).append(video)


# ---------------------------------------------------------------------------#
#                        RIPRODUZIONE E CALLBACK AUDIO                        #
# ---------------------------------------------------------------------------#
async def _play_song(ctx: commands.Context, video: dict):
    """Scarica il brano, riproduce e imposta callback per il successivo."""
    url = video.get("webpage_url") or f"https://www.youtube.com/watch?v={video.get('id')}"
    title = video.get("title", "Sconosciuto")

    # Download in directory temporanea dedicata
    temp_dir = tempfile.mkdtemp(prefix="yt_", dir=TEMP_PARENT)

    # CORREZIONE: Usa un nome file semplice e corto
    # Genera un nome file sicuro basato sull'ID del video o un timestamp
    video_id = video.get('id', str(int(time.time())))
    # Pulisci l'ID da caratteri problematici
    safe_id = ''.join(c for c in video_id if c.isalnum() or c in '-_')[:20]
    outtmpl = os.path.join(temp_dir, f"{safe_id}.%(ext)s")
    
    ydl_opts = build_ydl_opts({
        "outtmpl": outtmpl,
        # Aggiungi opzioni per evitare nomi file lunghi
        "restrictfilenames": True,
        "windowsfilenames": True,
    })

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            # Usa il nostro template personalizzato
            audio_path = outtmpl.replace("%(ext)s", info.get('ext', 'webm'))
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        await ctx.send(f"❌ Errore download: {e}")
        return

    try:
        source = discord.FFmpegPCMAudio(audio_path)
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        await ctx.send(f"❌ Errore audio: {e}")
        return

    vc = ctx.voice_client

    def _after_play(err):
        if err:
            print(f"[Player error] {err}")
        # Pulisci la cartella temporanea
        shutil.rmtree(temp_dir, ignore_errors=True)
        # Ricomincia con la traccia successiva
        bot.loop.create_task(_play_next(ctx))

    vc.play(source, after=_after_play)
    await ctx.send(f"▶️ Ora in riproduzione: **{title}**")

    # Aggiorna cronologia
    history_map.setdefault(ctx.guild.id, []).append(video)


async def _play_next(ctx: commands.Context):
    """Riproduce il prossimo brano in coda oppure esce dal canale."""
    queue = queue_map.get(ctx.guild.id)
    if not queue:
        await ctx.send("✅ Fine coda – disconnessione.")
        await ctx.voice_client.disconnect()
        return
    next_video = queue.popleft()
    await _play_song(ctx, next_video)


# ---------------------------------------------------------------------------#
#                                  EVENTI                                    #
# ---------------------------------------------------------------------------#
@bot.event
async def on_ready():
    print(f"✅ {bot.user} operativo.")


# ---------------------------------------------------------------------------#
#                              COMANDI DI RICERCA                            #
# ---------------------------------------------------------------------------#
@bot.command(name="search", aliases=["s"], help="Cerca i primi 10 video su YouTube.")
async def yt_search(ctx: commands.Context, *, query: str):
    async with ctx.typing():
        ydl_opts = build_ydl_opts(
            {
                "skip_download": True,
                "extract_flat": "in_playlist",
                "default_search": "ytsearch10",
                "forcejson": True,
            }
        )
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                result = ydl.extract_info(query, download=False)
                entries = result.get("entries", [])
        except Exception as e:
            await ctx.send(f"Errore ricerca: {e}")
            return

    if not entries:
        await ctx.send("⚠️ Nessun risultato.")
        return

    search_cache[ctx.guild.id] = entries
    elenco = "\n".join(
        f"{idx}. {v.get('title', 'Sconosciuto')[:80]} ({v.get('duration_string', 'LIVE')})"
        for idx, v in enumerate(entries, 1)
    )
    await ctx.send(
        f"**Risultati per:** '{query}'\n"
        f"'''\n{elenco}\n'''\n"
        "Usa '!play <numero>' per aggiungere alla coda."
    )


#@bot.command(name="search_playlist", aliases=["sytpl"], help="Cerca le prime 10 playlist su YouTube.")
async def yt_playlist_search(ctx: commands.Context, *, query: str):
    """Cerca playlist YouTube (10 risultati)."""
    async with ctx.typing():
        ydl_opts = build_ydl_opts(
            {
                "skip_download": True,
                "extract_flat": True,
                "default_search": "ytsearch10",
                "forcejson": True,
            }
        )
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                result = ydl.extract_info(f"{query} playlist", download=False)
                # Filtra solo risultati playlist
                entries = [e for e in result.get("entries", []) if e.get("_type") == "playlist"]
        except Exception as e:
            await ctx.send(f"Errore ricerca playlist: {e}")
            return

    if not entries:
        await ctx.send("⚠️ Nessuna playlist trovata.")
        return

    plist_cache[ctx.guild.id] = entries
    msg = "\n".join(f"{i}. {p.get('title','Sconosciuta')[:80]}" for i, p in enumerate(entries, 1))
    await ctx.send(
        f"**Playlist trovate:**\n"
        f"'''\n{msg}\n'''\n"
        "Usa '!playlist <numero>' per aggiungerla."
    )


# ---------------------------------------------------------------------------#
#                      COMANDI DI RIPRODUZIONE / CODA                         #
# ---------------------------------------------------------------------------#
@bot.command(name="play", aliases=["!"], help="Aggiunge video n dalla ricerca, URL o ricerca automatica.")
async def play(ctx: commands.Context, *, arg: str):
    vc = await ensure_voice(ctx)
    if vc is None:
        return

    # 1) Se è un indice
    if arg.isdigit():
        index = int(arg) - 1
        entries = search_cache.get(ctx.guild.id)
        if entries and 0 <= index < len(entries):
            video = entries[index]
            add_song(ctx.guild.id, video)
            await ctx.send(f"🎵 Aggiunto in coda: **{video.get('title','Sconosciuto')}**")
        else:
            await ctx.send("⚠️ Indice invalido o nessuna ricerca precedente.")
    # 2) Se è un URL
    elif arg.startswith("http://") or arg.startswith("https://"):
        try:
            info = yt_dlp.YoutubeDL(build_ydl_opts()).extract_info(arg, download=False)
        except Exception as e:
            await ctx.send(f"❌ Errore URL: {e}")
            return

        if info.get("_type") == "playlist" or "entries" in info:
            videos = info["entries"]
            for v in videos:
                add_song(ctx.guild.id, v)
            await ctx.send(f"📥 Playlist aggiunta: **{info.get('title','Sconosciuta')}** ({len(videos)} tracce).")
        else:
            add_song(ctx.guild.id, info)
            await ctx.send(f"🎵 Aggiunto in coda: **{info.get('title','Sconosciuto')}**")
    # 3) Fallback: cerca e prendi il primo risultato
    else:
        async with ctx.typing():
            ydl_opts = build_ydl_opts({
                "skip_download": True,
                "extract_flat": "in_playlist",
                "default_search": "ytsearch1",
                "forcejson": True,
            })
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    result = ydl.extract_info(arg, download=False)
                    entries = result.get("entries", [])
            except Exception as e:
                await ctx.send(f"❌ Errore ricerca automatica: {e}")
                return

        if not entries:
            await ctx.send("⚠️ Nessun risultato dalla ricerca automatica.")
            return

        video = entries[0]
        add_song(ctx.guild.id, video)
        await ctx.send(
            f"🔎 Non era indice né URL, ho cercato '**{arg}**' e aggiunto automaticamente: **{video.get('title','Sconosciuto')}**\n"
            "Per scegliere un video specifico usa `!search` e poi `!play <numero>`."
        )

    # Avvia riproduzione se fermo
    if not vc.is_playing() and not vc.is_paused():
        await _play_next(ctx)



#@bot.command(name="playlist", aliases=["ytpl"], help="Aggiunge playlist n dalla ricerca, URL o ricerca automatica.")
async def playlist_add(ctx: commands.Context, *, arg: str):
    # 1) Se è un indice
    if arg.isdigit():
        idx = int(arg) - 1
        entries = plist_cache.get(ctx.guild.id)
        if entries and 0 <= idx < len(entries):
            pl_info = entries[idx]
            try:
                full = yt_dlp.YoutubeDL(build_ydl_opts()).extract_info(pl_info["url"], download=False)
            except Exception as e:
                await ctx.send(f"❌ Errore caricamento playlist: {e}")
                return
            videos = full.get("entries", [])
            for v in videos:
                add_song(ctx.guild.id, v)
            await ctx.send(f"📥 Playlist aggiunta: **{full.get('title','Sconosciuta')}** ({len(videos)} tracce).")
        else:
            await ctx.send("⚠️ Indice invalido o nessuna ricerca playlist precedente.")
    # 2) Se è un URL
    elif arg.startswith("http://") or arg.startswith("https://"):
        try:
            info = yt_dlp.YoutubeDL(build_ydl_opts()).extract_info(arg, download=False)
        except Exception as e:
            await ctx.send(f"❌ Errore URL: {e}")
            return

        if info.get("_type") == "playlist" or "entries" in info:
            videos = info["entries"]
            for v in videos:
                add_song(ctx.guild.id, v)
            await ctx.send(f"📥 Playlist aggiunta: **{info.get('title','Sconosciuta')}** ({len(videos)} tracce).")
        else:
            await ctx.send("⚠️ L'URL non è una playlist valida.")
    # 3) Fallback: cerca playlist e prendi la prima
    else:
        async with ctx.typing():
            ydl_opts = build_ydl_opts({
                "skip_download": True,
                "extract_flat": True,
                "default_search": "ytsearch1",
                "forcejson": True,
            })
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    result = ydl.extract_info(f"{arg} playlist", download=False)
                    candidates = [e for e in result.get("entries", []) if e.get("_type") == "playlist"]
            except Exception as e:
                await ctx.send(f"❌ Errore ricerca playlist automatica: {e}")
                return

        if not candidates:
            await ctx.send("⚠️ Nessuna playlist trovata dalla ricerca automatica.")
            return

        pl = candidates[0]
        try:
            full = yt_dlp.YoutubeDL(build_ydl_opts()).extract_info(pl["url"], download=False)
        except Exception as e:
            await ctx.send(f"❌ Errore caricamento playlist trovata: {e}")
            return

        videos = full.get("entries", [])
        for v in videos:
            add_song(ctx.guild.id, v)
        await ctx.send(
            f"🔎 Non era indice né URL, ho cercato '**{arg}** playlist' e aggiunto automaticamente: **{full.get('title','Sconosciuta')}** "
            f"({len(videos)} tracce).\nPer scegliere una playlist specifica usa `!search_playlist` e poi `!playlist <numero>`."
        )

    # Se non sta già riproducendo, avvia
    vc = await ensure_voice(ctx)
    if vc and not vc.is_playing() and not vc.is_paused():
        await _play_next(ctx)


@bot.command(name="next", aliases=["skip"], help="Salta al prossimo brano.")
async def next_track(ctx: commands.Context):
    if ctx.voice_client is None or not ctx.voice_client.is_playing():
        await ctx.send("⏭️ Nulla in riproduzione.")
        return
    ctx.voice_client.stop()  # Triggera _after_play


@bot.command(name="previous", aliases=["prev"], help="Torna al brano precedente.")
async def previous_track(ctx: commands.Context):
    history = history_map.get(ctx.guild.id, [])
    if len(history) < 2:
        await ctx.send("⏮️ Nessun brano precedente.")
        return

    last_song = history[-2]
    # Rimuovi l’ultimo ascoltato e reinserisci il precedente in testa
    history_map[ctx.guild.id] = history[:-2]
    queue_map.setdefault(ctx.guild.id, deque()).appendleft(last_song)
    ctx.voice_client.stop()


@bot.command(name="queue", aliases=["q"], help="Mostra la coda corrente.")
async def show_queue(ctx: commands.Context):
    queue = list(queue_map.get(ctx.guild.id, deque()))
    if not queue:
        await ctx.send("📭 Coda vuota.")
        return

    msg = "\n".join(f"{i+1}. {s.get('title','Sconosciuto')[:80]}" for i, s in enumerate(queue))
    await ctx.send(f"🎶 **Coda:**\n'''\n{msg}\n'''")


@bot.command(name="clear", aliases=["c"], help="Pulisci la coda.")
async def clear_queue(ctx: commands.Context):
    """Svuota la coda."""
    queue_map[ctx.guild.id] = deque()
    await ctx.send("🗑️ Coda cancellata.")


@bot.command(name="pause", aliases=["p"], help="Metti in pausa la riproduzione (!resume per riprendere).")
async def pause(ctx: commands.Context):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.pause()
        await ctx.send("⏸️ Pausa.")
    else:
        await ctx.send("⚠️ Nulla in riproduzione.")


@bot.command(name="resume", aliases=["r"], help="Riprendi la riproduzione.")
async def resume(ctx: commands.Context):
    if ctx.voice_client and ctx.voice_client.is_paused():
        ctx.voice_client.resume()
        await ctx.send("▶️ Ripresa.")
    else:
        await ctx.send("⚠️ Nessuna canzone in pausa.")


@bot.command(name="leave", aliases=["stop"], help="Ferma la riproduzione e disconnetti il bot.")
async def leave(ctx: commands.Context):
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        await ctx.send("⏹️ Disconnesso.")
    else:
        await ctx.send("🔇 Il bot non è connesso.")


# ---------------------------------------------------------------------------#
#                                   MAIN                                     #
# ---------------------------------------------------------------------------#
if __name__ == "__main__":
    bot.run(TOKEN, log_handler=handler, log_level=logging.INFO)
