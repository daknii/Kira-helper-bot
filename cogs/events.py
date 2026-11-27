from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

if TYPE_CHECKING:
    from main import KiraBot

DEFAULT_COLOR = 0x2B2D31


class Events(commands.Cog):
    """Automação minimalista de eventos de entrada e saída de membros."""

    def __init__(self, bot: KiraBot) -> None:
        self.bot = bot

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
