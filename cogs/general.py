from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands


DEFAULT_COLOR = 0x2B2D31


class General(commands.Cog):
    """Comandos gerais do bot."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="ping", description="verifica a latência do bot.")
    async def ping(self, interaction: discord.Interaction) -> None:
        latency_ms = round(self.bot.latency * 1000)
        await interaction.response.send_message(
            embed=discord.Embed(
                description=f"latência · `{latency_ms}ms`",
                color=DEFAULT_COLOR,
            ),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(General(bot))
