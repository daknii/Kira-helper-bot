from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

if TYPE_CHECKING:
    from main import KiraBot

DEFAULT_COLOR = 0x2B2D31
TRAP_BAN_REASON = "enviou mensagem no canal armadilha (anti-spam)"
TRAP_DELETE_MESSAGE_SECONDS = 86400  # apaga mensagens das últimas 24h

logger = logging.getLogger("kira_bot.events")
_processing_trap_bans: set[tuple[int, int]] = set()


class Events(commands.Cog):
    """Automação minimalista de eventos de entrada, saída e canal armadilha."""

    def __init__(self, bot: KiraBot) -> None:
        self.bot = bot

    async def _send_trap_notifications(
        self,
        guild: discord.Guild,
        config: object,
        member: discord.Member,
        channel: discord.abc.GuildChannel | discord.Thread,
    ) -> None:
        """Envia o registro do banimento automático nos canais de logs e punições."""
        now = datetime.now(timezone.utc)

        log_channel_id = config["canal_logs"]  # type: ignore[index]
        if log_channel_id:
            log_channel = guild.get_channel(log_channel_id)
            if isinstance(log_channel, discord.TextChannel):
                log_embed = discord.Embed(
                    title="log · ban (armadilha)",
                    description="\n".join(
                        [
                            f"membro · `{member}` (`{member.id}`)",
                            f"canal · {channel.mention} (`{channel.id}`)",
                            f"motivo · {TRAP_BAN_REASON}",
                            "mensagens apagadas · `1 dia(s)`",
                        ]
                    ),
                    color=DEFAULT_COLOR,
                    timestamp=now,
                )
                log_embed.set_footer(text=guild.name.lower())
                try:
                    await log_channel.send(embed=log_embed)
                except (discord.Forbidden, discord.HTTPException):
                    pass

        punishments_channel_id = config["canal_punicoes"]  # type: ignore[index]
        if punishments_channel_id:
            punishments_channel = guild.get_channel(punishments_channel_id)
            if isinstance(punishments_channel, discord.TextChannel):
                moderator_text = guild.me.mention if guild.me else "automático"
                punishment_embed = discord.Embed(
                    title="registro de punição",
                    description="\n".join(
                        [
                            f"membro · `{member}` (`{member.id}`)",
                            "punição · `ban`",
                            f"motivo · {TRAP_BAN_REASON}",
                            f"moderador · {moderator_text}",
                        ]
                    ),
                    color=DEFAULT_COLOR,
                    timestamp=now,
                )
                punishment_embed.set_footer(text=guild.name.lower())
                try:
                    await punishments_channel.send(embed=punishment_embed)
                except (discord.Forbidden, discord.HTTPException):
                    pass

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if (
            message.guild is None
            or message.author.bot
            or message.webhook_id is not None
            or message.is_system()
        ):
            return

        if not isinstance(message.author, discord.Member):
            return

        guild = message.guild
        config = await self.bot.db.get_guild_config(guild.id)
        if not config or not config["canal_armadilha"]:
            return

        trap_channel_id = config["canal_armadilha"]
        channel_id = message.channel.id
        parent_id = getattr(message.channel, "parent_id", None)
        if channel_id != trap_channel_id and parent_id != trap_channel_id:
            return

        member = message.author
        # Dono do servidor, administradores e cargos staff são imunes
        if member.id == guild.owner_id or member.guild_permissions.administrator:
            return

        staff_ids = self.bot.db.parse_staff_roles(config["staff_role_ids"])
        if staff_ids and any(role.id in staff_ids for role in member.roles):
            return

        key = (guild.id, member.id)
        if key in _processing_trap_bans:
            return
        _processing_trap_bans.add(key)

        try:
            if guild.me and member.top_role >= guild.me.top_role:
                logger.warning(
                    "Canal armadilha: hierarquia insuficiente para banir %s (%s) em %s.",
                    member,
                    member.id,
                    guild.id,
                )
                return

            try:
                await member.ban(
                    reason=TRAP_BAN_REASON,
                    delete_message_seconds=TRAP_DELETE_MESSAGE_SECONDS,
                )
            except (discord.Forbidden, discord.HTTPException):
                logger.warning(
                    "Canal armadilha: falha ao banir %s (%s) em %s.",
                    member,
                    member.id,
                    guild.id,
                )
                return

            if isinstance(message.channel, (discord.abc.GuildChannel, discord.Thread)):
                await self._send_trap_notifications(guild, config, member, message.channel)
        finally:
            _processing_trap_bans.discard(key)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot:
            return

        guild = member.guild
        config = await self.bot.db.get_guild_config(guild.id)
        if not config or not config["canal_entrada"]:
            return

        channel = guild.get_channel(config["canal_entrada"])
        if not isinstance(channel, discord.TextChannel):
            return

        member_count = guild.member_count or len(guild.members)
        embed = discord.Embed(
            title="bem-vindo(a)",
            description=(
                f"olá {member.mention}, seja bem-vindo(a) a **{guild.name.lower()}**.\n"
                f"agora somos `{member_count}` membros."
            ),
            color=DEFAULT_COLOR,
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"{guild.name.lower()} · id {member.id}")

        try:
            await channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            pass

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        if member.bot:
            return

        guild = member.guild
        config = await self.bot.db.get_guild_config(guild.id)
        if not config or not config["canal_saida"]:
            return

        channel = guild.get_channel(config["canal_saida"])
        if not isinstance(channel, discord.TextChannel):
            return

        member_count = guild.member_count or len(guild.members)
        embed = discord.Embed(
            description=f"**{member.display_name.lower()}** (`{member}`) saiu do servidor.",
            color=DEFAULT_COLOR,
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text=f"{guild.name.lower()} · {member_count} membros restantes")

        try:
            await channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            pass


async def setup(bot: KiraBot) -> None:
    await bot.add_cog(Events(bot))
