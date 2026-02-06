import os
import re
import logging
import asyncio
import discord
from copy import copy
from typing import Optional
from yt_dlp import YoutubeDL
from yt_dlp.utils import YoutubeDLError
from redbot.core import commands, app_commands
from redbot.core.bot import Red, Config
from redbot.core.commands import Cog
from redbot.cogs.audio.core import Audio
from redbot.cogs.audio.utils import PlaylistScope
from redbot.cogs.audio.converters import PlaylistConverter, ScopeParser
from redbot.cogs.audio.apis.playlist_interface import get_all_playlist

log = logging.getLogger("red.crab-cogs.audioslash")

LANGUAGE = "de"
EXTRACT_CONFIG = {
    "extract_flat": True,
    "outtmpl": "%(title).85s.mp3",
    "extractor_args": {"youtube": {"lang": [LANGUAGE]}},
}
DOWNLOAD_CONFIG = {
    "extract_audio": True,
    "format": "bestaudio",
    "outtmpl": "%(title).85s.mp3",
    "extractor_args": {"youtube": {"lang": [LANGUAGE]}},
}
DOWNLOAD_FOLDER = "backup"
YOUTUBE_LINK_PATTERN = re.compile(r"(https?://)?(www\.)?(youtube.com/watch\?v=|youtu.be/)([\w\-]+)")
MAX_VIDEO_LENGTH = 2000

MAX_OPTIONS = 25
MAX_OPTION_SIZE = 100

async def extract_info(ydl: YoutubeDL, url: str) -> dict:
    return await asyncio.to_thread(ydl.extract_info, url, False)  # type: ignore

async def download_video(ydl: YoutubeDL, url: str) -> dict:
    return await asyncio.to_thread(ydl.extract_info, url)  # type: ignore

def format_youtube(res: dict) -> str:
    if res.get("duration", None):
        m, s = divmod(int(res['duration']), 60)
        name = f"({m}:{s:02d}) {res['title']}"
    else:
        name = f"(🔴LIVE) {res['title']}"
    
    author = f" — {res['channel']}"
    if len(author) > MAX_OPTION_SIZE // 2:
        author = author[:MAX_OPTION_SIZE//2 - 3] + "..."
    
    if len(name) + len(author) > MAX_OPTION_SIZE:
        return name[:MAX_OPTION_SIZE - len(author) - 3] + "..." + author
    else:
        return name + author


class AudioSlash(Cog):
    """Audio Modul in Form von Slash-Befehlen mit YouTube-Suche und Playlist-Verwaltung."""

    def __init__(self, bot: Red, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.bot = bot
        self.config = Config.get_conf(self, identifier=77241349)
        self.config.register_guild(**{"backup_mode": False})

    async def get_audio_cog(self, inter: discord.Interaction) -> Optional[Audio]:
        cog: Optional[Audio] = self.bot.get_cog("Audio")  # type: ignore
        if cog:
            return cog
        await inter.response.send_message("Audio Module nicht geladen! Kontaktiere den Bot-Besitzer für weitere Informationen.", ephemeral=True)

    async def get_context(self, inter: discord.Interaction, cog: Audio) -> commands.Context:
        ctx: commands.Context = await self.bot.get_context(inter)
        ctx.command.cog = cog
        return ctx

    async def can_run_command(self, ctx: commands.Context, command_name: str) -> bool:
        prefix = await self.bot.get_prefix(ctx.message)
        prefix = prefix[0] if isinstance(prefix, list) else prefix
        fake_message = copy(ctx.message)
        fake_message.content = prefix + command_name
        command = ctx.bot.get_command(command_name)
        ctx.command = command  # Automatically bind the correct command object to the parent context
        fake_context: commands.Context = await ctx.bot.get_context(fake_message)
        try:
            can = await command.can_run(fake_context, check_all_parents=True, change_permission_state=False)
        except commands.CommandError:
            can = False
        if not can:
            await ctx.send("Für diese Aktion hast du keine Berechtigung.", ephemeral=True)
        return can


    @app_commands.command()
    @app_commands.guild_only
    @app_commands.describe(search="Gebe mir einen Vorschlag, und ich suche das Beste für dich heraus.",
                           when="Du kannst diesen Titel wählen, und ich füge Ihn direkt hinzu.")
    @app_commands.choices(when=[app_commands.Choice(name="Song als letztes spielen.", value="end"),
                                app_commands.Choice(name="Song nachdem jetzigen spielen.", value="next"),
                                app_commands.Choice(name="Song jetzt starten.", value="now")])
    async def play(self, inter: discord.Interaction, search: str, when: Optional[str]):
        """Suche ein Lied was du magst von Youtube"""
        if not (audio := await self.get_audio_cog(inter)):
            return
        ctx = await self.get_context(inter, audio)
        assert ctx.guild
        search = search.strip()

        if await self.config.guild(ctx.guild).backup_mode():
            await inter.response.defer()
            
            if not audio.local_folder_current_path:
                await audio.localtracks_folder_exists(ctx)
                if not audio.local_folder_current_path:
                    await ctx.reply("Ich konnte den Lokalen Track-Ordner nicht finden. Versuche es bitte erneut")
                    return
                
            if not search.startswith(DOWNLOAD_FOLDER + "/"):
                if match := YOUTUBE_LINK_PATTERN.match(search):
                    search = match.group(0)
                else:
                    search = "ytsearch1:" + search
    
                (audio.local_folder_current_path / DOWNLOAD_FOLDER).mkdir(parents=True, exist_ok=True)
                ydl = YoutubeDL(EXTRACT_CONFIG)  # type: ignore
                video_info = await extract_info(ydl, search)
                if video_info.get("entries", None):
                    video_info = video_info["entries"][0]
        
                if "duration" not in video_info or video_info["duration"] > MAX_VIDEO_LENGTH:
                    await ctx.send("Das Video ist zulang, oder es existiert nicht!")
                    return
        
                filename = ydl.prepare_filename(video_info)  # type: ignore
                if not os.path.exists(filename):
                    await ctx.send(f"`{filename}` wird runtergeladen ...")
                    ydl = YoutubeDL(DOWNLOAD_CONFIG)  # type: ignore
                    os.chdir(audio.local_folder_current_path / DOWNLOAD_FOLDER)
                    await download_video(ydl, search)
                    
                search = DOWNLOAD_FOLDER + "/" + filename
                
        if when in ("next", "now"):
            if not await self.can_run_command(ctx, "bumpplay"):
                return
            await audio.command_bumpplay(ctx, when == "now", query=search)
        else:
            if not await self.can_run_command(ctx, "play"):
                return
            await audio.command_play(ctx, query=search)


    @app_commands.command()
    @app_commands.guild_only
    async def pause(self, inter: discord.Interaction):
        """Mache eine kurze Pause von der Musik."""
        if not (audio := await self.get_audio_cog(inter)):
            return
        ctx = await self.get_context(inter, audio)
        if not await self.can_run_command(ctx, "pause"):
            return
        await audio.command_pause(ctx)

    @app_commands.command()
    @app_commands.guild_only
    async def stop(self, inter: discord.Interaction):
        """Lasse alle Lieder jetzt stoppen."""
        if not (audio := await self.get_audio_cog(inter)):
            return
        ctx = await self.get_context(inter, audio)
        if not await self.can_run_command(ctx, "stop"):
            return
        await audio.command_stop(ctx)

    @app_commands.command()
    @app_commands.guild_only
    @app_commands.describe(position="Skippe zum nächsten Lied.")
    async def skip(self, inter: discord.Interaction, position: Optional[app_commands.Range[int, 1, 1000]]):
        """Skippe das aktuelle Lied, oder springe zu einem bestimmten Lied in der Warteschlange."""
        if not (audio := await self.get_audio_cog(inter)):
            return
        ctx = await self.get_context(inter, audio)
        if not await self.can_run_command(ctx, "skip"):
            return
        await audio.command_skip(ctx, position)

    @app_commands.command()
    @app_commands.guild_only
    async def queue(self, inter: discord.Interaction):
        """Zeigt die aktuelle Warteschlange an."""
        if not (audio := await self.get_audio_cog(inter)):
            return
        ctx = await self.get_context(inter, audio)
        if not await self.can_run_command(ctx, "queue"):
            return
        await audio.command_queue(ctx)

    toggle = [app_commands.Choice(name="Aktiv", value="1"),
              app_commands.Choice(name="Deaktiviert", value="0")]

    @app_commands.command()
    @app_commands.guild_only
    @app_commands.describe(volume="Neuer Lautstärke-Wert zwischen 1 und 150.")
    async def volume(self, inter: discord.Interaction, volume: app_commands.Range[int, 1, 150]):
        """Ändere die Lautstärke der Musik im Sprachchat."""
        if not (audio := await self.get_audio_cog(inter)):
            return
        ctx = await self.get_context(inter, audio)
        if not await self.can_run_command(ctx, "volume"):
            return
        await audio.command_volume(ctx, volume)

    @app_commands.command()
    @app_commands.guild_only
    @app_commands.describe(toggle="Aktiviere oder deaktiviere das Mischen der Tracks.")
    @app_commands.choices(toggle=toggle)
    async def shuffle(self, inter: discord.Interaction, toggle: str):
        """Setzet, ob die Warteschlange gemischt werden soll oder nicht."""
        if not (audio := await self.get_audio_cog(inter)):
            return
        ctx = await self.get_context(inter, audio)
        assert ctx.guild
        value = bool(int(toggle))
        if value != await audio.config.guild(ctx.guild).shuffle():
            if not await self.can_run_command(ctx, "shuffle"):
                return
            await audio.command_shuffle(ctx)
        else:
            embed = discord.Embed(title="Einstellung geändert", description="gemischt: " + ("Aktiviert" if value else "Deaktiviert"))
            await audio.send_embed_msg(ctx, embed=embed)

    @app_commands.command()
    @app_commands.guild_only
    @app_commands.describe(toggle="Aktiviere oder deaktiviere das Wiederholen der Tracks.")
    @app_commands.choices(toggle=toggle)
    async def repeat(self, inter: discord.Interaction, toggle: str):
        """Setzet, ob die Warteschlange wiederholt werden soll oder nicht."""
        if not (audio := await self.get_audio_cog(inter)):
            return
        ctx = await self.get_context(inter, audio)
        assert ctx.guild
        value = bool(int(toggle))
        if value != await audio.config.guild(ctx.guild).repeat():
            if not await self.can_run_command(ctx, "repeat"):
                return
            await audio.command_repeat(ctx)
        else:
            embed = discord.Embed(title="Einstellung geändert", description="Wiederholen: " + ("Aktiviert" if value else "Deaktiviert"))
            await audio.send_embed_msg(ctx, embed=embed)


    playlist = app_commands.Group(name="playlist", description="Playlist Befehle", guild_only=True)

    playlist_scopes = [app_commands.Choice(name="Personal", value="USERPLAYLIST"),
                       app_commands.Choice(name="Server", value="GUILDPLAYLIST"),
                       app_commands.Choice(name="Global", value="GLOBALPLAYLIST")]

    @staticmethod
    def get_scope_data(scope: Optional[str], ctx: commands.Context) -> ScopeParser:
        return [scope, ctx.author, ctx.guild, False]  # type: ignore

    @playlist.command(name="play")
    @app_commands.describe(playlist="Der Name der Playlist.",
                           shuffle="Ob die Playlist vor dem Abspielen gemischt werden soll.")
    async def playlist_play(self, inter: discord.Interaction, playlist: str, shuffle: Optional[bool]):
        """Startet eine bestehende Playlist im Sprachchat."""
        if not (audio := await self.get_audio_cog(inter)):
            return
        ctx = await self.get_context(inter, audio)
        assert ctx.guild and isinstance(ctx.author, discord.Member)
        if not await self.can_run_command(ctx, "playlist play"):
            return       
        enabled = False
        if shuffle is not None and shuffle != await audio.config.guild(ctx.guild).shuffle():
            dj_enabled = audio._dj_status_cache.setdefault(ctx.guild.id, await audio.config.guild(ctx.guild).dj_enabled())
            can_skip = await audio._can_instaskip(ctx, ctx.author)
            if not dj_enabled or can_skip and await self.can_run_command(ctx, "shuffle"):
                await audio.config.guild(ctx.guild).shuffle.set(shuffle)
                enabled = shuffle
        match = await PlaylistConverter().convert(ctx, playlist)
        await audio.command_playlist_start(ctx, match)
        if enabled:
            await audio.config.guild(ctx.guild).shuffle.set(False)

    @playlist.command(name="create")
    @app_commands.describe(name="Der Name der neuen Playlist. Darf keine Leerzeichen enthalten.",
                           type="Möchtest du die aktuelle Warteschlange als Playlist speichern?",
                           scope="Zu welchem Benutzer diese Playlist gehört. Du benötigst Berechtigungen für Server und Global.")
    @app_commands.choices(scope=playlist_scopes,
                          type=[app_commands.Choice(name="Leere Playlist", value="empty"),
                                app_commands.Choice(name="Aktuelle Warteschlange", value="queue")])
    async def playlist_create(self, inter: discord.Interaction, name: str, type: str, scope: Optional[str]):
        """Erstellt eine neue Playlist."""
        if not (audio := await self.get_audio_cog(inter)):
            return
        name = name.replace(" ", "-")
        ctx = await self.get_context(inter, audio)
        if type == "queue":
            if not await self.can_run_command(ctx, "playlist queue"):
                return
            await audio.command_playlist_queue(ctx, name, scope_data=self.get_scope_data(scope, ctx))
        else:
            if not await self.can_run_command(ctx, "playlist create"):
                return
            await audio.command_playlist_create(ctx, name, scope_data=self.get_scope_data(scope, ctx))

    @playlist.command(name="add")
    @app_commands.describe(playlist="Der Name der Playlist.",
                           track="Der Track, der zur Playlist hinzugefügt werden soll.",
                           scope="Du kannst angeben, zu welchem Benutzer diese Playlist gehört.")
    @app_commands.choices(scope=playlist_scopes)
    async def playlist_add(self, inter: discord.Interaction, playlist: str, track: str, scope: Optional[str]):
        """Fügt einen Track zu einer bestehenden Playlist hinzu."""
        if not (audio := await self.get_audio_cog(inter)):
            return
        ctx = await self.get_context(inter, audio)
        match = await PlaylistConverter().convert(ctx, playlist)
        if not await self.can_run_command(ctx, "playlist append"):
            return
        await audio.command_playlist_append(ctx, match, track, scope_data=self.get_scope_data(scope, ctx))

    @playlist.command(name="remove")
    @app_commands.describe(playlist="Der Name der Playlist.",
                           track="Der Link zum Track, der aus der Playlist entfernt werden soll.",
                           scope="Du kannst angeben, zu welchem Benutzer diese Playlist gehört.")
    @app_commands.choices(scope=playlist_scopes)
    async def playlist_remove(self, inter: discord.Interaction, playlist: str, track: str, scope: Optional[str]):
        """Entfernt einen Track aus einer bestehenden Playlist."""
        if not (audio := await self.get_audio_cog(inter)):
            return
        ctx = await self.get_context(inter, audio)
        match = await PlaylistConverter().convert(ctx, playlist)
        if not await self.can_run_command(ctx, "playlist remove"):
            return
        await audio.command_playlist_remove(ctx, match, track, scope_data=self.get_scope_data(scope, ctx))

    @playlist.command(name="info")
    @app_commands.describe(playlist="Der Name der anzuzeigenden Playlist.",
                           scope="Du kannst angeben, zu welchem Benutzer diese Playlist gehört.")
    @app_commands.choices(scope=playlist_scopes)
    async def playlist_info(self, inter: discord.Interaction, playlist: str, scope: Optional[str]):
        """Zeigt Informationen über eine Playlist an."""
        if not (audio := await self.get_audio_cog(inter)):
            return
        ctx = await self.get_context(inter, audio)
        match = await PlaylistConverter().convert(ctx, playlist)
        if not await self.can_run_command(ctx, "playlist info"):
            return
        await audio.command_playlist_info(ctx, match, scope_data=self.get_scope_data(scope, ctx))

    @playlist.command(name="delete")
    @app_commands.describe(playlist="Der Name der zu löschenden Playlist.",
                           scope="Du kannst angeben, zu welchem Benutzer diese Playlist gehört.")
    @app_commands.choices(scope=playlist_scopes)
    async def playlist_delete(self, inter: discord.Interaction, playlist: str, scope: Optional[str]):
        """Löscht eine Playlist vollständig."""
        if not (audio := await self.get_audio_cog(inter)):
            return
        ctx = await self.get_context(inter, audio)
        match = await PlaylistConverter().convert(ctx, playlist)
        if not await self.can_run_command(ctx, "playlist delete"):
            return
        await audio.command_playlist_delete(ctx, match, scope_data=self.get_scope_data(scope, ctx))


    @play.autocomplete("search")
    @playlist_add.autocomplete("track")
    async def youtube_autocomplete(self, inter: discord.Interaction, current: str):
        try:
            return await self._youtube_autocomplete(inter, current)
        except Exception:  # noqa, reason: user-facing error
            log.exception("YouTube autocomplete", stack_info=True)
            return [app_commands.Choice(name="Autocomplete Fehler. Kontaktiere einen Bot-Mitarbeiter", value=".")]

    async def _youtube_autocomplete(self, inter: discord.Interaction, current: str):
        assert inter.guild
        lst = []

        if await self.config.guild(inter.guild).backup_mode():
            audio = await self.get_audio_cog(inter)
            if audio and audio.local_folder_current_path:
                folder = (audio.local_folder_current_path / DOWNLOAD_FOLDER)
                folder.mkdir(parents=True, exist_ok=True)
                files = [app_commands.Choice(name=filename, value=f"{DOWNLOAD_FOLDER}/{filename}"[:MAX_OPTION_SIZE]) for
                        filename in os.listdir(folder)]
                if current:
                    lst += [file for file in files if file.name.lower().startswith(current.lower())]
                    lst += [file for file in files if
                            current.lower() in file.name.lower() and not file.name.lower().startswith(current.lower())]
                else:
                    lst += files

        if not current or len(current) < 3 or len(lst) >= MAX_OPTIONS:
            return lst[:MAX_OPTIONS]

        try:
            ydl = YoutubeDL(EXTRACT_CONFIG)  # type: ignore
            results = await extract_info(ydl, f"ytsearch{MAX_OPTIONS - len(lst)}:{current}")
            lst += [app_commands.Choice(name=format_youtube(res), value=res["url"]) for res in results["entries"]]
        except YoutubeDLError:
            log.exception("Retrieving youtube results", stack_info=True)

        return lst[:MAX_OPTIONS]


    @playlist_play.autocomplete("playlist")
    @playlist_add.autocomplete("playlist")
    @playlist_remove.autocomplete("playlist")
    @playlist_info.autocomplete("playlist")
    @playlist_delete.autocomplete("playlist")
    async def playlist_autocomplete(self, inter: discord.Interaction, current: str):
        try:
            return await self._playlist_autocomplete(inter, current)
        except Exception:  # noqa, reason: user-facing error
            log.exception("Playlist autocomplete")
            return [app_commands.Choice(name="Autocomplete Fehler. Kontaktiere einen Bot-Mitarbeiter", value=".")]

    async def _playlist_autocomplete(self, inter: discord.Interaction, current: str):
        audio: Optional[Audio] = self.bot.get_cog("Audio")  # type: ignore
        if not audio or not audio.playlist_api:
            return []

        global_matches = await get_all_playlist(
            PlaylistScope.GLOBAL.value, self.bot, audio.playlist_api, inter.guild, inter.user
        )
        guild_matches = await get_all_playlist(
            PlaylistScope.GUILD.value, self.bot, audio.playlist_api, inter.guild, inter.user
        )
        user_matches = await get_all_playlist(
            PlaylistScope.USER.value, self.bot, audio.playlist_api, inter.guild, inter.user
        )
        playlists = [*user_matches, *guild_matches, *global_matches]

        if current:
            results = [pl.name for pl in playlists if pl.name.lower().startswith(current.lower())]
            results += [pl.name for pl in playlists if
                        current.lower() in pl.name.lower() and not pl.name.lower().startswith(current.lower())]
        else:
            results = [pl.name for pl in playlists]

        return [app_commands.Choice(name=pl, value=pl) for pl in results][:MAX_OPTIONS]


    @commands.is_owner()
    @commands.command(name="audioslashbackupmode", hidden=True)
    async def audioslashbackupmode(self, ctx: commands.Context, value: Optional[bool]):
        """Aktiviert oder deaktiviert den Backup-Modus, in dem YouTube-Videos heruntergeladen und als lokale Dateien abgespielt werden, anstatt sie direkt von YouTube zu streamen. Dies kann nützlich sein, wenn es Probleme mit der Verbindung zu YouTube gibt oder wenn"""
        assert ctx.guild
        if value is None:
            value = await self.config.guild(ctx.guild).backup_mode()
        else:
            await self.config.guild(ctx.guild).backup_mode.set(value)
        await ctx.reply(f"Backup mode: `{value}`", mention_author=False)
