from __future__ import annotations

import asyncio
import io
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, cast

import discord
from discord import app_commands
from discord.ext import commands

if TYPE_CHECKING:
    from main import KiraBot

DEFAULT_COLOR = 0x2B2D31
TOPIC_USER_RE = re.compile(r"user_id:(\d+)")
CHANNEL_SLUG_RE = re.compile(r"[^a-z0-9-]+")

_closing_channels: set[int] = set()


def _sanitize_username(username: str, fallback_id: int) -> str:
    slug = CHANNEL_SLUG_RE.sub("-", username.lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return (slug or str(fallback_id))[:80]


def _extract_owner_id(topic: str | None) -> int | None:
    if not topic:
        return None
    match = TOPIC_USER_RE.search(topic)
    return int(match.group(1)) if match else None


async def _build_transcript(channel: discord.TextChannel) -> tuple[io.BytesIO, int]:
    """Gera o arquivo .txt em memória com todo o histórico do canal."""
    lines: list[str] = [
        f"transcript · #{channel.name} ({channel.id})",
        f"servidor · {channel.guild.name} ({channel.guild.id})",
        f"gerado em · {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "-" * 60,
    ]

    message_count = 0
    async for msg in channel.history(limit=None, oldest_first=True):
        message_count += 1
        timestamp = msg.created_at.strftime("%Y-%m-%d %H:%M:%S")
        author = f"{msg.author} ({msg.author.id})"
        content = msg.content.replace("\n", "\n    ") if msg.content else ""

        if content:
            lines.append(f"[{timestamp}] {author}: {content}")
        else:
            lines.append(f"[{timestamp}] {author}:")

        for attachment in msg.attachments:
            lines.append(f"    [anexo] {attachment.filename}: {attachment.url}")

        for embed in msg.embeds:
            if embed.title or embed.description:
                summary = " — ".join(
                    part for part in (embed.title, embed.description) if part
                )
                lines.append(f"    [embed] {summary[:200]}")

    payload = "\n".join(lines).encode("utf-8")
    return io.BytesIO(payload), message_count


class TicketCloseView(discord.ui.View):
    """View persistente exibida dentro de cada ticket aberto."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="fechar ticket",
        style=discord.ButtonStyle.danger,
        custom_id="ticket:close",
    )
    async def close_ticket(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[TicketCloseView],
    ) -> None:
        channel = interaction.channel
        guild = interaction.guild

        if not isinstance(channel, discord.TextChannel) or guild is None:
            await interaction.response.send_message(
                embed=discord.Embed(
                    description="este botão só pode ser usado em um canal de ticket.",
                    color=DEFAULT_COLOR,
                ),
                ephemeral=True,
            )
            return

        if channel.id in _closing_channels:
            await interaction.response.send_message(
                embed=discord.Embed(
                    description="este ticket já está sendo fechado.",
                    color=DEFAULT_COLOR,
                ),
                ephemeral=True,
            )
            return

        _closing_channels.add(channel.id)
        try:
            await interaction.response.send_message(
                embed=discord.Embed(
                    description="gerando transcript e fechando ticket em 5 segundos...",
                    color=DEFAULT_COLOR,
                )
            )

            bot = cast("KiraBot", interaction.client)
            config = await bot.db.get_guild_config(guild.id)

            transcript_buffer, message_count = await _build_transcript(channel)

            if config and config["canal_logs"]:
                log_channel = guild.get_channel(config["canal_logs"])
                if isinstance(log_channel, discord.TextChannel):
                    owner_id = _extract_owner_id(channel.topic)
                    owner_text = f"<@{owner_id}> (`{owner_id}`)" if owner_id else "desconhecido"

                    log_embed = discord.Embed(
                        title="ticket encerrado",
                        description="\n".join(
                            [
                                f"canal · `#{channel.name}`",
                                f"aberto por · {owner_text}",
                                f"fechado por · {interaction.user.mention} (`{interaction.user.id}`)",
                                f"mensagens · `{message_count}`",
                            ]
                        ),
                        color=DEFAULT_COLOR,
                        timestamp=datetime.now(timezone.utc),
                    )
                    log_embed.set_footer(text=guild.name.lower())

                    transcript_file = discord.File(
                        fp=transcript_buffer,
                        filename=f"transcript-{channel.name}.txt",
                    )
                    await log_channel.send(embed=log_embed, file=transcript_file)

            await asyncio.sleep(5)
            await channel.delete(reason=f"ticket fechado por {interaction.user}")
        finally:
            _closing_channels.discard(channel.id)


class TicketOpenView(discord.ui.View):
    """View persistente do painel principal de atendimento."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="abrir ticket",
        style=discord.ButtonStyle.secondary,
        custom_id="ticket:open",
    )
    async def open_ticket(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[TicketOpenView],
    ) -> None:
        guild = interaction.guild
        user = interaction.user

        if guild is None or not isinstance(user, discord.Member):
            await interaction.response.send_message(
                embed=discord.Embed(
                    description="esta ação só pode ser usada dentro de um servidor.",
                    color=DEFAULT_COLOR,
                ),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        bot = cast("KiraBot", interaction.client)
        config = await bot.db.get_guild_config(guild.id)
        staff_ids = await bot.db.get_staff_roles(guild.id)

        if not config or not config["categoria_tickets"] or not staff_ids:
            await interaction.followup.send(
                embed=discord.Embed(
                    description="sistema de tickets não configurado. peça a um administrador para usar `/configurar` ou `/add-staff`.",
                    color=DEFAULT_COLOR,
                ),
                ephemeral=True,
            )
            return

        category = guild.get_channel(config["categoria_tickets"])
        staff_roles = [
            role for r_id in staff_ids if (role := guild.get_role(r_id)) is not None
        ]

        if not isinstance(category, discord.CategoryChannel) or not staff_roles:
            await interaction.followup.send(
                embed=discord.Embed(
                    description="categoria de tickets ou cargos staff não encontrados. reconfigure com `/configurar`.",
                    color=DEFAULT_COLOR,
                ),
                ephemeral=True,
            )
            return

        # Impede mais de um ticket simultâneo por usuário
        marker = f"user_id:{user.id}"
        for existing in guild.text_channels:
            if existing.topic and marker in existing.topic:
                await interaction.followup.send(
                    embed=discord.Embed(
                        description=f"você já possui um ticket aberto em {existing.mention}.",
                        color=DEFAULT_COLOR,
                    ),
                    ephemeral=True,
                )
                return

        overwrites: dict[discord.Role | discord.Member, discord.PermissionOverwrite] = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(
                view_channel=True,
                read_messages=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True,
            ),
        }
        for staff_role in staff_roles:
            overwrites[staff_role] = discord.PermissionOverwrite(
                view_channel=True,
                read_messages=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True,
                manage_messages=True,
            )
        if guild.me is not None:
            overwrites[guild.me] = discord.PermissionOverwrite(
                view_channel=True,
                read_messages=True,
                send_messages=True,
                read_message_history=True,
                manage_channels=True,
                manage_messages=True,
                attach_files=True,
                embed_links=True,
            )

        channel_name = f"ticket-{_sanitize_username(user.name, user.id)}"
        ticket_channel = await guild.create_text_channel(
            name=channel_name,
            category=category,
            topic=f"ticket · {marker}",
            overwrites=overwrites,
            reason=f"ticket aberto por {user}",
        )

        staff_mentions = " ".join(f"<@&{r.id}>" for r in staff_roles)
        welcome_embed = discord.Embed(
            title="atendimento iniciado",
            description=(
                f"olá {user.mention}, descreva o motivo do seu contato com o máximo de detalhes.\n"
                f"a equipe {staff_mentions} responderá assim que possível."
            ),
            color=DEFAULT_COLOR,
        )
        welcome_embed.set_footer(text=f"{guild.name.lower()} · use o botão abaixo para encerrar")

        await ticket_channel.send(
            content=f"{user.mention} {staff_mentions}",
            embed=welcome_embed,
            view=TicketCloseView(),
        )

        await interaction.followup.send(
            embed=discord.Embed(
                description=f"seu ticket foi criado em {ticket_channel.mention}.",
                color=DEFAULT_COLOR,
            ),
            ephemeral=True,
        )


class Tickets(commands.Cog):
    """Sistema completo de tickets com Views persistentes e transcript automático."""

    def __init__(self, bot: KiraBot) -> None:
        self.bot = bot

    @app_commands.command(
        name="setup-tickets",
        description="envia o painel minimalista de suporte e abertura de tickets no canal atual.",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def setup_tickets(self, interaction: discord.Interaction) -> None:
        if not isinstance(interaction.channel, discord.abc.Messageable):
            await interaction.response.send_message(
                embed=discord.Embed(
                    description="não foi possível enviar o painel neste canal.",
                    color=DEFAULT_COLOR,
                ),
                ephemeral=True,
            )
            return

        guild_name = interaction.guild.name.lower() if interaction.guild else "comunidade"
        panel_embed = discord.Embed(
            title="suporte & atendimento",
            description=(
                "precisa de ajuda, tirar alguma dúvida ou fazer uma denúncia?\n"
                "clique no botão abaixo para abrir um canal privado com a equipe."
            ),
            color=DEFAULT_COLOR,
        )
        panel_embed.set_footer(text=f"{guild_name} · atendimento privado")

        await interaction.channel.send(embed=panel_embed, view=TicketOpenView())
        await interaction.response.send_message(
            embed=discord.Embed(
                description="painel de tickets enviado neste canal.",
                color=DEFAULT_COLOR,
            ),
            ephemeral=True,
        )


async def setup(bot: KiraBot) -> None:
    await bot.add_cog(Tickets(bot))
