import asyncio
import os
import shutil
import re
import time
import io
import logging
from collections import deque
from typing import Dict, List, Optional, Any, Set, Tuple

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from api.resolver import resolve_music
from api.youtube import get_youtube_info, get_video_comments

load_dotenv()

def get_discord_tokens() -> List[str]:
    tokens = []
    multi_tokens = os.getenv("DISCORD_TOKENS", "").strip()
    if multi_tokens:
        for t in multi_tokens.split(","):
            cleaned = t.strip()
            if cleaned and cleaned not in tokens:
                tokens.append(cleaned)

    single_token = os.getenv("DISCORD_TOKEN", "").strip()
    if single_token and single_token not in tokens:
        tokens.append(single_token)

    for i in range(1, 21):
        numbered_token = os.getenv(f"DISCORD_TOKEN_{i}", "").strip()
        if numbered_token and numbered_token not in tokens:
            tokens.append(numbered_token)

    return tokens

DISCORD_TOKENS = get_discord_tokens()
FFMPEG_CUSTOM_PATH = os.getenv("FFMPEG_PATH", "")

def get_ffmpeg_executable() -> str:
    if FFMPEG_CUSTOM_PATH and os.path.isfile(FFMPEG_CUSTOM_PATH):
        return FFMPEG_CUSTOM_PATH
    local_ffmpeg = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ffmpeg.exe")
    if os.path.isfile(local_ffmpeg):
        return local_ffmpeg
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if os.path.isfile(exe):
            return exe
    except Exception:
        pass
    return shutil.which("ffmpeg") or "ffmpeg"

FFMPEG_EXECUTABLE = get_ffmpeg_executable()

FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn'
}

PLATFORM_COLORS = {
    'YouTube': discord.Color.from_rgb(255, 0, 0),
    'Spotify': discord.Color.from_rgb(30, 215, 96),
    'Apple Music': discord.Color.from_rgb(252, 60, 68),
    'Melon': discord.Color.from_rgb(0, 205, 60)
}

def format_duration(seconds: int) -> str:
    if not seconds or seconds < 0:
        return "00:00"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"

def render_progress_bar(current: int, total: int, length: int = 14) -> str:
    if total <= 0:
        return "[O" + "=" * (length - 1) + "]"
    fraction = min(1.0, max(0.0, current / total))
    point = min(length - 1, int(fraction * (length - 1)))
    bar = ["="] * length
    bar[point] = "O"
    return "[" + "".join(bar) + "]"

class PasswordModal(discord.ui.Modal, title="🔒 패스워드 인증"):
    password_input = discord.ui.TextInput(
        label="패스워드를 입력하세요",
        placeholder="이 세션에 설정된 패스워드를 입력하세요",
        required=True,
        max_length=50
    )

    def __init__(self, music_state: "GuildMusicState", on_success_callback=None):
        super().__init__()
        self.music_state = music_state
        self.on_success_callback = on_success_callback

    async def on_submit(self, interaction: discord.Interaction):
        entered = self.password_input.value.strip()
        if self.music_state.password and entered == self.music_state.password:
            self.music_state.authenticated_users.add(interaction.user.id)
            if self.on_success_callback:
                await self.on_success_callback(interaction)
            else:
                await interaction.response.send_message("패스워드가 인증되었습니다.", ephemeral=True)
        else:
            await interaction.response.send_message("패스워드가 일치하지 않습니다.", ephemeral=True)

class SeekModal(discord.ui.Modal, title="⏱️ 시간대 직접 이동"):
    time_input = discord.ui.TextInput(
        label="이동할 시간 (예: 1:30 또는 초 단위 90)",
        placeholder="예: 01:20 또는 75",
        required=True,
        max_length=10
    )

    def __init__(self, music_state: "GuildMusicState"):
        super().__init__()
        self.music_state = music_state

    async def on_submit(self, interaction: discord.Interaction):
        val = self.time_input.value.strip()
        target_seconds = 0
        try:
            if ':' in val:
                parts = list(map(int, val.split(':')))
                if len(parts) == 2:
                    target_seconds = parts[0] * 60 + parts[1]
                elif len(parts) == 3:
                    target_seconds = parts[0] * 3600 + parts[1] * 60 + parts[2]
            else:
                target_seconds = int(val)
        except Exception:
            await interaction.response.send_message("올바른 시간 형식이 아닙니다. (예: 1:30 또는 90)", ephemeral=True)
            return

        await self.music_state.seek_to(target_seconds, interaction)

class AddSongModal(discord.ui.Modal, title="🎵 새 곡 추가하기"):
    query_input = discord.ui.TextInput(
        label="곡 제목 또는 URL (단일 곡 / 재생목록)",
        placeholder="YouTube, Spotify, Apple Music, Melon 링크나 제목을 입력하세요",
        required=True,
        max_length=300
    )

    def __init__(self, music_state: "GuildMusicState"):
        super().__init__()
        self.music_state = music_state

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        query = self.query_input.value.strip()
        result = await resolve_music(query)

        if not result:
            await interaction.followup.send(f"'{query}' 에 대한 음악을 찾을 수 없습니다.", ephemeral=True)
            return

        if result.get('is_playlist'):
            items = result.get('items', [])
            for item in items:
                item['requester'] = interaction.user
                self.music_state.queue.append(item)

            if not self.music_state.is_actively_playing:
                await interaction.followup.send(
                    f"재생목록 {result.get('playlist_title')} (총 {len(items)}곡) 재생을 시작합니다.",
                    ephemeral=True
                )
                asyncio.create_task(self.music_state.play_next())
            else:
                await interaction.followup.send(
                    f"재생목록 {result.get('playlist_title')} (총 {len(items)}곡)이 대기열에 추가되었습니다.",
                    ephemeral=True
                )
            return

        song_info = result
        song_info['requester'] = interaction.user

        if self.music_state.is_actively_playing:
            self.music_state.queue.append(song_info)
            embed = discord.Embed(
                title=f"📥 대기열 추가: {song_info['title']}",
                url=song_info.get('webpage_url') or song_info.get('source_url'),
                color=PLATFORM_COLORS.get(song_info.get('platform'), discord.Color.blue())
            )
            embed.add_field(name="🎧 플랫폼", value=song_info.get('platform', 'Unknown'), inline=True)
            embed.add_field(name="🔢 대기 순서", value=f"{len(self.music_state.queue)}번째", inline=True)
            if song_info.get('thumbnail'):
                embed.set_thumbnail(url=song_info['thumbnail'])
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            self.music_state.queue.append(song_info)
            await interaction.followup.send(f"▶️ {song_info['title']} 재생을 시작합니다.", ephemeral=True)
            asyncio.create_task(self.music_state.play_next())

class SongInfoView(discord.ui.View):
    def __init__(self, music_state: "GuildMusicState"):
        super().__init__(timeout=180)
        self.music_state = music_state
        self.setup_timeline_select()

    def setup_timeline_select(self):
        cur = self.music_state.current
        duration = cur.get('duration', 0) if cur else 0
        if duration <= 10:
            return

        options = []
        percentages = [0, 20, 40, 60, 80]
        for p in percentages:
            sec = int(duration * (p / 100))
            options.append(
                discord.SelectOption(
                    label=f"{p}% 지점 ({format_duration(sec)})",
                    value=str(sec),
                    emoji="⏱️",
                    description=f"{format_duration(sec)} 위치로 이동"
                )
            )

        select = discord.ui.Select(
            placeholder="⏱️ 타임바 위치 선택 (원하는 구간으로 이동)",
            options=options,
            row=0
        )

        async def select_callback(interaction: discord.Interaction):
            sec = int(select.values[0])
            await self.music_state.seek_to(sec, interaction)

        select.callback = select_callback
        self.add_item(select)

    @discord.ui.button(label="-30초", emoji="⏪", style=discord.ButtonStyle.secondary, row=1)
    async def btn_rewind(self, interaction: discord.Interaction, button: discord.ui.Button):
        current_pos = self.music_state.get_current_position()
        target = max(0, current_pos - 30)
        await self.music_state.seek_to(target, interaction)

    @discord.ui.button(label="+30초", emoji="⏩", style=discord.ButtonStyle.secondary, row=1)
    async def btn_fast_forward(self, interaction: discord.Interaction, button: discord.ui.Button):
        current_pos = self.music_state.get_current_position()
        target = current_pos + 30
        await self.music_state.seek_to(target, interaction)

    @discord.ui.button(label="시간 직접 입력", emoji="⏱️", style=discord.ButtonStyle.primary, row=1)
    async def btn_manual_seek(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(SeekModal(self.music_state))

    @discord.ui.button(label="댓글 보기", emoji="💬", style=discord.ButtonStyle.success, row=1)
    async def btn_fetch_comments(self, interaction: discord.Interaction, button: discord.ui.Button):
        cur = self.music_state.current
        if not cur or cur.get('platform') != 'YouTube' or not cur.get('webpage_url'):
            await interaction.response.send_message("유튜브 곡이 아니거나 댓글을 불러올 수 없습니다.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        comments = await get_video_comments(cur['webpage_url'], max_comments=5)

        embed = discord.Embed(
            title=f"💬 유튜브 댓글 Top 5 - {cur.get('title')}",
            color=discord.Color.red()
        )
        if comments:
            for i, c in enumerate(comments, 1):
                embed.add_field(
                    name=f"👍 {i}. {c['author']} (좋아요 {c['like_count']})",
                    value=c['text'] or "(내용 없음)",
                    inline=False
                )
        else:
            embed.description = "댓글을 불러올 수 없거나 댓글이 비활성화되어 있습니다."

        await interaction.followup.send(embed=embed, ephemeral=True)

class QueueSelectView(discord.ui.View):
    def __init__(self, music_state: "GuildMusicState"):
        super().__init__(timeout=120)
        self.music_state = music_state

        options = []
        for idx, song in enumerate(list(self.music_state.queue)[:25]):
            title = song.get('title', 'Unknown')[:80]
            dur = format_duration(song.get('duration', 0))
            options.append(
                discord.SelectOption(
                    label=f"{idx + 1}. {title}"[:100],
                    value=str(idx),
                    emoji="🎵",
                    description=f"길이: {dur}"
                )
            )

        if options:
            select = discord.ui.Select(
                placeholder="🎵 재생할 노래를 선택하세요 (즉시 건너뛰기)",
                options=options,
                row=0
            )

            async def select_callback(interaction: discord.Interaction):
                selected_idx = int(select.values[0])
                if selected_idx < len(self.music_state.queue):
                    target_song = self.music_state.queue[selected_idx]
                    del self.music_state.queue[selected_idx]
                    self.music_state.queue.appendleft(target_song)

                    await interaction.response.send_message(
                        f"⏭️ 선택하신 {target_song['title']}(으)로 건너뜁니다.",
                        ephemeral=True
                    )
                    if self.music_state.voice_client:
                        self.music_state.voice_client.stop()
                else:
                    await interaction.response.send_message("유효하지 않은 번호입니다.", ephemeral=True)

            select.callback = select_callback
            self.add_item(select)

class MusicControlView(discord.ui.View):
    def __init__(self, music_state: "GuildMusicState"):
        super().__init__(timeout=None)
        self.music_state = music_state
        self.update_pause_resume_button()

    def update_pause_resume_button(self):
        is_paused = (
            self.music_state.voice_client is not None
            and self.music_state.voice_client.is_paused()
        )
        self.btn_stop_toggle.label = "재생" if is_paused else "중지"
        self.btn_stop_toggle.emoji = "▶️" if is_paused else "⏸️"
        self.btn_stop_toggle.style = (
            discord.ButtonStyle.success if is_paused else discord.ButtonStyle.secondary
        )

    async def _check_access(self, interaction: discord.Interaction, success_callback) -> bool:
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message("먼저 음성 채널에 입장해 주세요.", ephemeral=True)
            return False

        if (
            self.music_state.voice_client
            and self.music_state.voice_client.channel != interaction.user.voice.channel
        ):
            await interaction.response.send_message(
                f"봇이 접속해 있는 음성 채널({self.music_state.voice_client.channel.name})과 다른 채널에 계십니다.",
                ephemeral=True
            )
            return False

        if not self.music_state.password:
            return True

        if interaction.user.guild_permissions.administrator:
            return True

        if interaction.user.id in self.music_state.authenticated_users:
            return True

        await interaction.response.send_modal(PasswordModal(self.music_state, success_callback))
        return False

    @discord.ui.button(label="곡 추가", emoji="➕", style=discord.ButtonStyle.primary, row=0)
    async def btn_add_song(self, interaction: discord.Interaction, button: discord.ui.Button):
        async def do_add(itx: discord.Interaction):
            await itx.response.send_modal(AddSongModal(self.music_state))

        if await self._check_access(interaction, do_add):
            await do_add(interaction)

    @discord.ui.button(label="중지", emoji="⏸️", style=discord.ButtonStyle.secondary, row=0)
    async def btn_stop_toggle(self, interaction: discord.Interaction, button: discord.ui.Button):
        async def do_toggle(itx: discord.Interaction):
            vc = self.music_state.voice_client
            if not vc:
                await itx.response.send_message("봇이 음성 채널에 연결되어 있지 않습니다.", ephemeral=True)
                return

            if vc.is_paused():
                vc.resume()
                self.music_state.playback_paused_duration += (time.time() - self.music_state.pause_start_time)
                self.music_state.cancel_inactivity_timer()
                self.music_state.start_progress_updater()
                self.update_pause_resume_button()
                await itx.response.edit_message(view=self)
            elif vc.is_playing():
                vc.pause()
                self.music_state.pause_start_time = time.time()
                self.music_state.start_inactivity_timer()
                self.update_pause_resume_button()
                await itx.response.edit_message(view=self)
            else:
                await itx.response.send_message("재생 중인 음악이 없습니다.", ephemeral=True)

        if await self._check_access(interaction, do_toggle):
            await do_toggle(interaction)

    @discord.ui.button(label="곡 건너뛰기", emoji="⏭️", style=discord.ButtonStyle.secondary, row=0)
    async def btn_skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        async def do_skip(itx: discord.Interaction):
            vc = self.music_state.voice_client
            if not vc or not self.music_state.is_playing:
                await itx.response.send_message("현재 재생 중인 음악이 없습니다.", ephemeral=True)
                return
            vc.stop()

        if await self._check_access(interaction, do_skip):
            await do_skip(interaction)

    @discord.ui.button(label="Info", emoji="ℹ️", style=discord.ButtonStyle.secondary, row=0)
    async def btn_info(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.music_state.current:
            await interaction.response.send_message("현재 재생 중인 음악이 없습니다.", ephemeral=True)
            return

        cur = self.music_state.current
        duration = cur.get('duration', 0)
        current_pos = self.music_state.get_current_position()
        remaining = max(0, duration - current_pos) if duration > 0 else 0

        progress_bar = render_progress_bar(current_pos, duration)
        time_text = f"`{progress_bar}` {format_duration(current_pos)} / {format_duration(duration)} (남은 시간: {format_duration(remaining)})"

        platform = cur.get('platform', 'YouTube')
        color = PLATFORM_COLORS.get(platform, discord.Color.blue())

        embed = discord.Embed(
            title=f"ℹ️ Info: {cur.get('title')}",
            url=cur.get('webpage_url') or cur.get('source_url'),
            color=color,
            description=(
                f"🎤 아티스트: {cur.get('artist', '알 수 없음')}\n"
                f"🎧 플랫폼: {platform} | 👤 신청자: {cur.get('requester').mention if cur.get('requester') else '알 수 없음'}\n\n"
                f"📊 재생 진행률:\n{time_text}"
            )
        )
        if cur.get('thumbnail'):
            embed.set_thumbnail(url=cur['thumbnail'])

        info_view = SongInfoView(self.music_state)
        await interaction.response.send_message(embed=embed, view=info_view, ephemeral=True)

    @discord.ui.button(label="대기열", emoji="📜", style=discord.ButtonStyle.secondary, row=1)
    async def btn_queue(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)

        embed = discord.Embed(title="📜 재생 대기열", color=discord.Color.blue())
        if self.music_state.current:
            cur = self.music_state.current
            embed.add_field(
                name="▶️ 현재 재생 중",
                value=f"[{cur['title']}]({cur.get('webpage_url') or cur.get('source_url')}) ({cur.get('platform')})",
                inline=False
            )

        queue_list = list(self.music_state.queue)
        file = None

        if queue_list:
            display_list = []
            for idx, song in enumerate(queue_list[:10], start=1):
                display_list.append(f"{idx}. {song['title']} [{song.get('platform')}] ({format_duration(song.get('duration', 0))})")

            if len(queue_list) > 10:
                display_list.append(f"...외 {len(queue_list) - 10}곡 (전체 목록은 첨부 파일 참조)")
                txt_content = f"=== 전체 재생 대기열 목록 (총 {len(queue_list)}곡) ===\n\n"
                if self.music_state.current:
                    cur = self.music_state.current
                    txt_content += f"[현재 재생 중] {cur.get('title')} ({cur.get('platform')}) - {format_duration(cur.get('duration', 0))}\n\n"
                for idx, song in enumerate(queue_list, start=1):
                    txt_content += f"{idx:02d}. {song.get('title')} - {song.get('artist', '알 수 없음')} [{song.get('platform')}] ({format_duration(song.get('duration', 0))})\n    링크: {song.get('webpage_url') or song.get('source_url')}\n"

                file = discord.File(
                    fp=io.BytesIO(txt_content.encode('utf-8')),
                    filename="대기열_목록.txt"
                )

            embed.add_field(name="📋 다음 재생 예정", value="\n".join(display_list), inline=False)
        else:
            embed.add_field(name="📋 다음 재생 예정", value="대기열이 비어 있습니다.", inline=False)

        select_view = QueueSelectView(self.music_state)
        if file:
            await interaction.followup.send(embed=embed, file=file, view=select_view, ephemeral=True)
        else:
            await interaction.followup.send(embed=embed, view=select_view, ephemeral=True)

    @discord.ui.button(label="퇴장", emoji="🚪", style=discord.ButtonStyle.danger, row=1)
    async def btn_leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        async def do_leave(itx: discord.Interaction):
            if self.music_state.voice_client:
                leave_msg = "음성 채널에서 퇴장했습니다."
                try:
                    await itx.response.edit_message(content=leave_msg, embed=None, view=None)
                    self.music_state.current_controller_msg = None
                except Exception:
                    try:
                        await itx.response.defer()
                    except Exception:
                        pass
                await self.music_state.disconnect(reason=leave_msg)
            else:
                await itx.response.send_message("봇이 음성 채널에 있지 않습니다.", ephemeral=True)

        if await self._check_access(interaction, do_leave):
            await do_leave(interaction)

class GuildMusicState:
    def __init__(self, bot: commands.Bot, guild_id: int):
        self.bot = bot
        self.guild_id = guild_id
        self.queue: deque = deque()
        self.current: Optional[Dict[str, Any]] = None
        self.voice_client: Optional[discord.VoiceClient] = None
        self.is_playing: bool = False
        self.text_channel: Optional[discord.abc.Messageable] = None
        self.current_controller_msg: Optional[discord.Message] = None
        self.lock = asyncio.Lock()
        self.password: Optional[str] = None
        self.authenticated_users: Set[int] = set()
        self.inactivity_task: Optional[asyncio.Task] = None
        self.progress_task: Optional[asyncio.Task] = None

        self.playback_start_time: float = 0.0
        self.pause_start_time: float = 0.0
        self.playback_paused_duration: float = 0.0
        self.is_seeking: bool = False

    @property
    def is_actively_playing(self) -> bool:
        return bool(
            self.voice_client
            and self.voice_client.is_connected()
            and (self.voice_client.is_playing() or self.voice_client.is_paused())
            and self.current is not None
        )

    def get_current_position(self) -> int:
        if not self.is_playing or self.playback_start_time == 0:
            return 0
        if self.voice_client and self.voice_client.is_paused():
            elapsed = self.pause_start_time - self.playback_start_time - self.playback_paused_duration
        else:
            elapsed = time.time() - self.playback_start_time - self.playback_paused_duration
        return max(0, int(elapsed))

    def start_progress_updater(self):
        self.cancel_progress_updater()

        async def updater():
            last_pos = -1
            while self.is_playing and self.voice_client and self.voice_client.is_connected():
                await asyncio.sleep(1)
                if not self.is_playing or not self.voice_client:
                    break
                if self.voice_client.is_paused():
                    continue
                if not self.current or not self.current_controller_msg:
                    continue

                current_pos = self.get_current_position()
                if current_pos == last_pos:
                    continue
                last_pos = current_pos

                try:
                    embed = self.create_now_playing_embed(self.current)
                    await self.current_controller_msg.edit(embed=embed)
                except discord.RateLimited as e:
                    await asyncio.sleep(e.retry_after)
                except discord.HTTPException as e:
                    if e.status == 429:
                        retry_after = 1.5
                        try:
                            retry_after = float(e.response.headers.get('Retry-After', 1.5))
                        except Exception:
                            pass
                        await asyncio.sleep(retry_after)
                    elif e.status == 404:
                        break
                except Exception:
                    pass

        self.progress_task = asyncio.create_task(updater())

    def cancel_progress_updater(self):
        if self.progress_task and not self.progress_task.done():
            self.progress_task.cancel()
            self.progress_task = None

    def start_inactivity_timer(self):
        self.cancel_inactivity_timer()

        async def timer_task():
            try:
                await asyncio.sleep(180)
                if not self.is_playing and self.voice_client and self.voice_client.is_connected():
                    await self.disconnect(reason="👋 3분 동안 활동이 없어 음성 채널에서 퇴장했습니다. 대기열이 초기화되었습니다.")
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

        self.inactivity_task = asyncio.create_task(timer_task())

    def cancel_inactivity_timer(self):
        if self.inactivity_task and not self.inactivity_task.done():
            curr = None
            try:
                curr = asyncio.current_task()
            except Exception:
                pass
            if curr != self.inactivity_task:
                self.inactivity_task.cancel()
        self.inactivity_task = None

    def reset_state(self):
        self.cancel_inactivity_timer()
        self.cancel_progress_updater()
        self.queue.clear()
        self.is_playing = False
        self.current = None
        self.password = None
        self.authenticated_users.clear()
        self.current_controller_msg = None
        self.playback_start_time = 0.0
        self.pause_start_time = 0.0
        self.playback_paused_duration = 0.0
        self.is_seeking = False

    async def disconnect(self, reason: str = "음성 채널에서 퇴장했습니다."):
        self.cancel_inactivity_timer()
        self.cancel_progress_updater()
        self.queue.clear()
        self.is_playing = False
        self.current = None
        self.password = None
        self.authenticated_users.clear()

        if self.current_controller_msg:
            try:
                await self.current_controller_msg.edit(content=reason, embed=None, view=None)
            except Exception:
                if self.text_channel:
                    try:
                        await self.text_channel.send(reason)
                    except Exception:
                        pass
            self.current_controller_msg = None
        elif self.text_channel:
            try:
                await self.text_channel.send(reason)
            except Exception:
                pass

        vc = self.voice_client
        self.voice_client = None

        if vc:
            try:
                await vc.disconnect(force=True)
            except Exception:
                pass

        guild = self.bot.get_guild(self.guild_id)
        if guild and guild.voice_client:
            try:
                await guild.voice_client.disconnect(force=True)
            except Exception:
                pass

    async def seek_to(self, seconds: int, interaction: Optional[discord.Interaction] = None):
        if not self.current or not self.voice_client or not self.voice_client.is_connected():
            if interaction:
                await interaction.response.send_message("현재 재생 중인 곡이 없습니다.", ephemeral=True)
            return

        total_dur = self.current.get('duration', 0)
        target_sec = max(0, min(seconds, total_dur - 1 if total_dur > 1 else 0))

        self.is_seeking = True
        self.voice_client.stop()
        await asyncio.sleep(0.2)

        try:
            stream_url = self.current['stream_url']
            before_opts = f"-ss {target_sec} -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
            source = discord.FFmpegPCMAudio(
                stream_url,
                executable=FFMPEG_EXECUTABLE,
                before_options=before_opts,
                options='-vn'
            )
            audio = discord.PCMVolumeTransformer(source, volume=0.8)

            def after_playing(error):
                if self.is_seeking:
                    return
                asyncio.run_coroutine_threadsafe(self.play_next(), self.bot.loop)

            self.voice_client.play(audio, after=after_playing)
            self.is_playing = True
            self.is_seeking = False

            self.playback_start_time = time.time() - target_sec
            self.pause_start_time = 0.0
            self.playback_paused_duration = 0.0

            if self.current_controller_msg:
                try:
                    await self.current_controller_msg.edit(embed=self.create_now_playing_embed(self.current))
                except Exception:
                    pass

            if interaction:
                await interaction.response.send_message(
                    f"{format_duration(target_sec)} 위치로 이동 완료했습니다.",
                    ephemeral=True
                )
        except Exception:
            self.is_seeking = False
            if interaction:
                await interaction.response.send_message("시간대 이동에 실패했습니다.", ephemeral=True)

    def create_now_playing_embed(self, song: Dict[str, Any]) -> discord.Embed:
        platform = song.get('platform', 'YouTube')
        color = PLATFORM_COLORS.get(platform, discord.Color.blue())

        lock_badge = " 🔒 [보안 세션]" if self.password else ""

        duration = song.get('duration', 0)
        current_pos = self.get_current_position()
        remaining = max(0, duration - current_pos) if duration > 0 else 0
        progress_bar = render_progress_bar(current_pos, duration)
        time_text = f"`{progress_bar}` {format_duration(current_pos)} / {format_duration(duration)} (남은 시간: {format_duration(remaining)})"

        embed = discord.Embed(
            title=f"🎵 {song.get('title')}{lock_badge}",
            url=song.get('webpage_url') or song.get('source_url'),
            color=color,
            description=(
                f"🎤 아티스트: {song.get('artist', '알 수 없음')}\n"
                f"🎧 플랫폼: {platform} | 👤 신청자: {song.get('requester').mention if song.get('requester') else '알 수 없음'}\n"
                f"🤖 담당 봇: {self.bot.user.name}\n\n"
                f"📊 재생 진행률:\n{time_text}"
            )
        )
        if song.get('thumbnail'):
            embed.set_image(url=song['thumbnail'])

        footer_text = "✨ 아래 버튼을 눌러 컨트롤하거나 곡을 추가하세요."
        if self.password:
            footer_text += " (🔒 패스워드 보호 중)"
        embed.set_footer(text=footer_text)
        return embed

    async def play_next(self, initial_interaction: Optional[discord.Interaction] = None):
        async with self.lock:
            self.cancel_progress_updater()

            if not self.voice_client or not self.voice_client.is_connected():
                self.is_playing = False
                self.current = None
                if initial_interaction and not initial_interaction.is_expired():
                    try:
                        await initial_interaction.followup.send("음성 채널에 연결되어 있지 않습니다.")
                    except Exception:
                        pass
                return

            if self.voice_client.is_playing() or self.voice_client.is_paused():
                try:
                    self.voice_client.stop()
                except Exception:
                    pass
                for _ in range(15):
                    if not self.voice_client.is_playing():
                        break
                    await asyncio.sleep(0.1)

            if not self.queue:
                was_playing = self.is_playing
                self.is_playing = False
                self.current = None
                if was_playing and self.text_channel:
                    try:
                        await self.text_channel.send("✨ 모든 대기열의 곡 재생이 완료되었습니다. (3분 뒤 자동 퇴장)")
                    except Exception:
                        pass
                if initial_interaction and not initial_interaction.is_expired():
                    try:
                        await initial_interaction.followup.send("대기열이 비어 있습니다.")
                    except Exception:
                        pass
                self.start_inactivity_timer()
                return

            self.cancel_inactivity_timer()

            next_song = self.queue.popleft()
            self.current = next_song

            try:
                stream_url = next_song.get('stream_url')
                if not stream_url:
                    yt_res = await get_youtube_info(next_song.get('webpage_url') or next_song.get('source_url'))
                    if yt_res and yt_res.get('stream_url'):
                        stream_url = yt_res['stream_url']
                        next_song['stream_url'] = stream_url
                        if not next_song.get('thumbnail') and yt_res.get('thumbnail'):
                            next_song['thumbnail'] = yt_res['thumbnail']
                    else:
                        raise Exception("스트림 URL을 가져올 수 없습니다.")

                source = discord.FFmpegPCMAudio(
                    stream_url,
                    executable=FFMPEG_EXECUTABLE,
                    **FFMPEG_OPTIONS
                )
                audio = discord.PCMVolumeTransformer(source, volume=0.8)

                def after_playing(error):
                    if error:
                        logging.getLogger("discord.client").error(f"FFmpeg 재생 오류: {error}")
                    if self.is_seeking:
                        return
                    asyncio.run_coroutine_threadsafe(self.play_next(), self.bot.loop)

                self.voice_client.play(audio, after=after_playing)
                self.is_playing = True

                self.playback_start_time = time.time()
                self.pause_start_time = 0.0
                self.playback_paused_duration = 0.0

                embed = self.create_now_playing_embed(next_song)
                view = MusicControlView(self)

                updated = False
                if self.current_controller_msg:
                    try:
                        await self.current_controller_msg.edit(embed=embed, view=view)
                        updated = True
                    except (discord.NotFound, discord.HTTPException):
                        self.current_controller_msg = None

                if not updated:
                    if initial_interaction and not initial_interaction.is_expired():
                        try:
                            self.current_controller_msg = await initial_interaction.followup.send(embed=embed, view=view)
                            updated = True
                        except Exception:
                            pass
                    if not updated and self.text_channel:
                        try:
                            self.current_controller_msg = await self.text_channel.send(embed=embed, view=view)
                        except Exception:
                            pass
                elif initial_interaction and not initial_interaction.is_expired():
                    try:
                        await initial_interaction.delete_original_response()
                    except Exception:
                        pass

                self.start_progress_updater()

            except Exception as e:
                logging.getLogger("discord.client").exception(f"{next_song.get('title')} 재생 처리 중 오류 발생")
                err_text = f"{next_song.get('title')} 재생에 실패했습니다: {e}"
                if initial_interaction and not initial_interaction.is_expired():
                    try:
                        await initial_interaction.followup.send(err_text)
                    except Exception:
                        if self.text_channel:
                            try:
                                await self.text_channel.send(err_text)
                            except Exception:
                                pass
                elif self.text_channel:
                    try:
                        await self.text_channel.send(err_text)
                    except Exception:
                        pass

                self.is_playing = False
                self.current = None
                if self.queue:
                    asyncio.create_task(self.play_next())
                else:
                    self.start_inactivity_timer()

class BotWorker:
    def __init__(self, token: str, is_master: bool = False):
        intents = discord.Intents.default()
        intents.message_content = True
        self.bot = commands.Bot(command_prefix="!", intents=intents)
        self.token = token
        self.is_master = is_master
        self.states: Dict[int, GuildMusicState] = {}

    def get_state(self, guild: discord.Guild) -> GuildMusicState:
        if guild.id not in self.states:
            self.states[guild.id] = GuildMusicState(self.bot, guild.id)
        return self.states[guild.id]

bot_pool: List[BotWorker] = []

def find_active_worker_for_channel(guild_id: int, channel_id: int) -> Optional[Tuple[BotWorker, GuildMusicState]]:
    for worker in bot_pool:
        if worker.bot.is_ready():
            state = worker.states.get(guild_id)
            if state and state.voice_client and state.voice_client.is_connected():
                if state.voice_client.channel.id == channel_id:
                    return (worker, state)
    return None

def find_idle_worker(guild_id: int) -> Optional[Tuple[BotWorker, GuildMusicState]]:
    for worker in bot_pool:
        if worker.bot.is_ready():
            state = worker.states.get(guild_id)
            if not state or not state.voice_client or not state.voice_client.is_connected():
                guild = worker.bot.get_guild(guild_id)
                if guild:
                    return (worker, worker.get_state(guild))
    return None

def setup_master_slash_commands(master_worker: BotWorker):
    master_bot = master_worker.bot

    @master_bot.tree.command(
        name="재생",
        description="음악을 재생합니다."
    )
    @app_commands.describe(
        query="노래 제목 또는 URL (YouTube 영상/재생목록, Spotify, Apple Music, Melon)",
        password="선택: 컨트롤러 버튼 조작 제한용 패스워드"
    )
    async def slash_play(interaction: discord.Interaction, query: str, password: Optional[str] = None):
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message("먼저 음성 채널에 입장해 주세요.", ephemeral=True)
            return

        user_channel = interaction.user.voice.channel
        guild = interaction.guild

        await interaction.response.defer()

        worker = None
        state = None
        try:
            active_pair = find_active_worker_for_channel(guild.id, user_channel.id)
            if active_pair:
                worker, state = active_pair
            else:
                idle_pair = find_idle_worker(guild.id)
                if not idle_pair:
                    await interaction.followup.send(
                        f"현재 서버의 모든 음악 봇(총 {len(bot_pool)}대)이 다른 음성 채널에서 사용 중입니다.\n"
                        "다른 채널의 재생이 끝난 후 다시 시도해 주세요."
                    )
                    return
                worker, state = idle_pair

                try:
                    state.reset_state()
                    target_channel = worker.bot.get_channel(user_channel.id)
                    if not target_channel:
                        try:
                            target_channel = await worker.bot.fetch_channel(user_channel.id)
                        except Exception:
                            target_channel = user_channel
                    state.voice_client = await target_channel.connect()
                except Exception as e:
                    await interaction.followup.send(f"음성 채널 접속 실패: {e}")
                    return

            state.text_channel = interaction.channel

            if password and password.strip():
                state.password = password.strip()
                state.authenticated_users.add(interaction.user.id)

            result = await resolve_music(query)
            if not result:
                await interaction.followup.send(f"'{query}' 에 대한 음악을 찾을 수 없습니다.")
                if not state.is_actively_playing and not state.queue:
                    await state.disconnect()
                return

            if result.get('is_playlist'):
                items = result.get('items', [])
                for item in items:
                    item['requester'] = interaction.user
                    state.queue.append(item)

                if state.is_actively_playing:
                    embed = discord.Embed(
                        title=f"📑 재생목록 대기열 추가: {result.get('playlist_title')}",
                        color=discord.Color.gold(),
                        description=f"총 {len(items)}곡이 대기열에 순차적으로 추가되었습니다.\n🤖 담당 봇: {worker.bot.user.name}"
                    )
                    if items and items[0].get('thumbnail'):
                        embed.set_thumbnail(url=items[0]['thumbnail'])
                    await interaction.followup.send(embed=embed)
                else:
                    asyncio.create_task(state.play_next(initial_interaction=interaction))
                return

            song_info = result
            song_info['requester'] = interaction.user

            if state.is_actively_playing:
                state.queue.append(song_info)
                embed = discord.Embed(
                    title=f"📥 대기열 추가: {song_info['title']}",
                    url=song_info.get('webpage_url') or song_info.get('source_url'),
                    color=PLATFORM_COLORS.get(song_info.get('platform'), discord.Color.blue())
                )
                embed.add_field(name="🎧 플랫폼", value=song_info.get('platform', 'Unknown'), inline=True)
                embed.add_field(name="🔢 대기 번호", value=f"{len(state.queue)}번째", inline=True)
                embed.add_field(name="🤖 담당 봇", value=f"{worker.bot.user.name}", inline=True)
                if song_info.get('thumbnail'):
                    embed.set_thumbnail(url=song_info['thumbnail'])
                await interaction.followup.send(embed=embed)
            else:
                state.queue.append(song_info)
                asyncio.create_task(state.play_next(initial_interaction=interaction))

        except Exception as e:
            logging.getLogger("discord.client").exception("slash_play 처리 중 오류 발생")
            try:
                await interaction.followup.send(f"명령어 처리 중 오류가 발생했습니다: {e}")
            except Exception:
                pass
            if state and not state.is_actively_playing and not state.queue:
                try:
                    await state.disconnect()
                except Exception:
                    pass

async def start_bot_pool():
    if not DISCORD_TOKENS:
        print("등록된 DISCORD_TOKEN이 없습니다.")
        return

    for idx, token in enumerate(DISCORD_TOKENS):
        is_master = (idx == 0)
        worker = BotWorker(token, is_master=is_master)

        def make_on_ready(w: BotWorker, index: int, master: bool):
            async def on_ready():
                if master:
                    try:
                        await w.bot.tree.sync()
                    except Exception:
                        pass
                await w.bot.change_presence(
                    activity=discord.Activity(
                        type=discord.ActivityType.listening,
                        name=f"🎶 /재생 | 봇 {index + 1}호기"
                    )
                )
                logging.getLogger("discord.client").info(
                    f"봇 로그인 완료: {w.bot.user.name} (봇 {index + 1}호기 준비됨)"
                )
            return on_ready

        worker.bot.event(make_on_ready(worker, idx, is_master))
        bot_pool.append(worker)

    setup_master_slash_commands(bot_pool[0])

    tasks = [worker.bot.start(worker.token) for worker in bot_pool]
    await asyncio.gather(*tasks)

def main():
    discord.utils.setup_logging(level=logging.INFO)
    try:
        asyncio.run(start_bot_pool())
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
