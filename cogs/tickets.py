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
TOPIC_DIVISION_RE = re.compile(r"divisao:(aprendiz|samurai)")
CHANNEL_SLUG_RE = re.compile(r"[^a-z0-9-]+")

_closing_channels: set[int] = set()
_opening_tickets: set[tuple[int, int]] = set()
_opening_division_tickets: set[tuple[int, int]] = set()
_evaluating_channels: set[int] = set()


def _sanitize_username(username: str, fallback_id: int) -> str:
    slug = CHANNEL_SLUG_RE.sub("-", username.lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return (slug or str(fallback_id))[:80]


def _extract_owner_id(topic: str | None) -> int | None:
    if not topic:
        return None
    match = TOPIC_USER_RE.search(topic)
    return int(match.group(1)) if match else None


def _extract_division(topic: str | None) -> str | None:
    if not topic:
        return None
    match = TOPIC_DIVISION_RE.search(topic)
    return match.group(1) if match else None


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


async def _close_ticket_channel(interaction: discord.Interaction) -> None:
    """Fluxo compartilhado para encerrar um canal de ticket ou teste de divisão."""
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

    owner_id = _extract_owner_id(channel.topic)
    if owner_id is None:
        await interaction.response.send_message(
            embed=discord.Embed(
                description="este canal não é um ticket.",
                color=DEFAULT_COLOR,
            ),
            ephemeral=True,
        )
        return

    bot = cast("KiraBot", interaction.client)
    member = interaction.user
    allowed = member.id == owner_id
    if isinstance(member, discord.Member) and not allowed:
        if member.guild_permissions.manage_channels:
            allowed = True
        else:
            staff_ids = await bot.db.get_staff_roles(guild.id)
            allowed = any(role.id in staff_ids for role in member.roles)
    if not allowed:
        await interaction.response.send_message(
            embed=discord.Embed(
                description="só quem abriu o ticket ou a staff pode fechá-lo.",
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
                description="gerando transcript e fechando o ticket...",
                color=DEFAULT_COLOR,
            )
        )

        config = await bot.db.get_guild_config(guild.id)
        try:
            transcript_buffer, message_count = await _build_transcript(channel)
        except discord.HTTPException:
            transcript_buffer, message_count = None, 0

        if config and config["canal_logs"] and transcript_buffer is not None:
            log_channel = guild.get_channel(config["canal_logs"])
            if isinstance(log_channel, discord.TextChannel):
                owner_text = f"<@{owner_id}> (`{owner_id}`)"
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
                try:
                    await log_channel.send(embed=log_embed, file=transcript_file)
                except (discord.Forbidden, discord.HTTPException):
                    pass

        await asyncio.sleep(5)
        try:
            await channel.delete(reason=f"ticket fechado por {interaction.user}")
        except (discord.Forbidden, discord.HTTPException):
            await interaction.followup.send(
                embed=discord.Embed(
                    description="não foi possível apagar o canal do ticket. verifique as permissões do bot.",
                    color=DEFAULT_COLOR,
                ),
                ephemeral=True,
            )
    finally:
        _closing_channels.discard(channel.id)


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
        await _close_ticket_channel(interaction)


class DivisionTicketControlView(discord.ui.View):
    """View persistente exibida dentro de cada canal de teste de divisão."""

    def __init__(self, *, evaluated: bool = False) -> None:
        super().__init__(timeout=None)
        if evaluated:
            self.approve_test.disabled = True
            self.reject_test.disabled = True

    @discord.ui.button(
        label="aprovar",
        style=discord.ButtonStyle.success,
        custom_id="divisao:approve",
    )
    async def approve_test(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[DivisionTicketControlView],
    ) -> None:
        await self._evaluate(interaction, approved=True)

    @discord.ui.button(
        label="reprovar",
        style=discord.ButtonStyle.danger,
        custom_id="divisao:reject",
    )
    async def reject_test(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[DivisionTicketControlView],
    ) -> None:
        await self._evaluate(interaction, approved=False)

    @discord.ui.button(
        label="fechar ticket",
        style=discord.ButtonStyle.secondary,
        custom_id="divisao:close",
    )
    async def close_ticket(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[DivisionTicketControlView],
    ) -> None:
        await _close_ticket_channel(interaction)

    async def _evaluate(
        self,
        interaction: discord.Interaction,
        *,
        approved: bool,
    ) -> None:
        channel = interaction.channel
        guild = interaction.guild
        evaluator = interaction.user

        if (
            not isinstance(channel, discord.TextChannel)
            or guild is None
            or not isinstance(evaluator, discord.Member)
        ):
            await interaction.response.send_message(
                embed=discord.Embed(
                    description="este botão só pode ser usado em um canal de teste de divisão.",
                    color=DEFAULT_COLOR,
                ),
                ephemeral=True,
            )
            return

        owner_id = _extract_owner_id(channel.topic)
        division_key = _extract_division(channel.topic)
        if owner_id is None or division_key is None:
            await interaction.response.send_message(
                embed=discord.Embed(
                    description="este canal não é um teste de divisão válido.",
                    color=DEFAULT_COLOR,
                ),
                ephemeral=True,
            )
            return

        bot = cast("KiraBot", interaction.client)
        staff_ids = await bot.db.get_staff_roles(guild.id)
        is_staff = (
            evaluator.guild_permissions.administrator
            or evaluator.guild_permissions.manage_roles
            or any(role.id in staff_ids for role in evaluator.roles)
        )
        if not is_staff:
            await interaction.response.send_message(
                embed=discord.Embed(
                    description="apenas membros da staff podem avaliar este teste.",
                    color=DEFAULT_COLOR,
                ),
                ephemeral=True,
            )
            return

        if channel.id in _evaluating_channels:
            await interaction.response.send_message(
                embed=discord.Embed(
                    description="este teste já está sendo processado.",
                    color=DEFAULT_COLOR,
                ),
                ephemeral=True,
            )
            return

        _evaluating_channels.add(channel.id)
        try:
            config = await bot.db.get_guild_config(guild.id)
            division_label = "aprendiz" if division_key == "aprendiz" else "samurai"

            target = guild.get_member(owner_id)
            if target is None:
                try:
                    target = await guild.fetch_member(owner_id)
                except (discord.NotFound, discord.HTTPException):
                    target = None

            if approved:
                if target is None:
                    await interaction.response.send_message(
                        embed=discord.Embed(
                            description="o membro não está mais no servidor para receber o cargo.",
                            color=DEFAULT_COLOR,
                        ),
                        ephemeral=True,
                    )
                    return

                if division_key == "aprendiz":
                    new_role_id = config["cargo_aprendiz"] if config else None
                    old_role_id = None
                else:
                    new_role_id = config["cargo_samurai"] if config else None
                    old_role_id = config["cargo_aprendiz"] if config else None

                if not new_role_id:
                    await interaction.response.send_message(
                        embed=discord.Embed(
                            description=(
                                f"o cargo de **{division_label}** não está configurado. "
                                "reconfigure o painel usando `/setup-divisoes`."
                            ),
                            color=DEFAULT_COLOR,
                        ),
                        ephemeral=True,
                    )
                    return

                new_role = guild.get_role(new_role_id)
                if new_role is None:
                    await interaction.response.send_message(
                        embed=discord.Embed(
                            description=(
                                f"o cargo de **{division_label}** configurado não foi encontrado no servidor. "
                                "reconfigure usando `/setup-divisoes`."
                            ),
                            color=DEFAULT_COLOR,
                        ),
                        ephemeral=True,
                    )
                    return

                bot_member = guild.me
                if (
                    bot_member is None
                    or not bot_member.guild_permissions.manage_roles
                    or bot_member.top_role <= new_role
                ):
                    await interaction.response.send_message(
                        embed=discord.Embed(
                            description=(
                                f"não tenho permissão para atribuir {new_role.mention}. "
                                "verifique se o bot tem **gerenciar cargos** e está acima desse cargo na hierarquia."
                            ),
                            color=DEFAULT_COLOR,
                        ),
                        ephemeral=True,
                    )
                    return

                try:
                    await target.add_roles(
                        new_role,
                        reason=f"aprovado no teste de divisão ({division_label}) por {evaluator}",
                    )
                    if old_role_id:
                        old_role = guild.get_role(old_role_id)
                        if (
                            old_role is not None
                            and old_role in target.roles
                            and bot_member.top_role > old_role
                        ):
                            await target.remove_roles(
                                old_role,
                                reason=f"promovido para {division_label} por {evaluator}",
                            )
                except (discord.Forbidden, discord.HTTPException):
                    await interaction.response.send_message(
                        embed=discord.Embed(
                            description=f"falha ao atribuir o cargo {new_role.mention}. verifique as permissões do bot.",
                            color=DEFAULT_COLOR,
                        ),
                        ephemeral=True,
                    )
                    return

                result_embed = discord.Embed(
                    title=f"❖ teste aprovado · {division_label}",
                    description="\n".join(
                        [
                            f"{target.mention} foi **aprovado(a)** no teste para **{division_label}**!",
                            f"cargo atribuído · {new_role.mention}",
                            f"avaliado por · {evaluator.mention}",
                        ]
                    ),
                    color=DEFAULT_COLOR,
                    timestamp=datetime.now(timezone.utc),
                )
                result_embed.set_footer(
                    text=f"{guild.name.lower()} · use fechar ticket quando desejar encerrar"
                )
                status_text = "aprovado"
                role_line = f"cargo atribuído · {new_role.mention}"
            else:
                target_mention = target.mention if target else f"<@{owner_id}>"
                result_embed = discord.Embed(
                    title=f"❖ teste reprovado · {division_label}",
                    description="\n".join(
                        [
                            f"{target_mention} **não passou** no teste para **{division_label}** desta vez.",
                            "continue treinando e tente novamente quando estiver pronto(a).",
                            f"avaliado por · {evaluator.mention}",
                        ]
                    ),
                    color=DEFAULT_COLOR,
                    timestamp=datetime.now(timezone.utc),
                )
                result_embed.set_footer(
                    text=f"{guild.name.lower()} · use fechar ticket quando desejar encerrar"
                )
                status_text = "reprovado"
                role_line = None

            await interaction.response.send_message(embed=result_embed)

            if interaction.message is not None:
                try:
                    await interaction.message.edit(
                        view=DivisionTicketControlView(evaluated=True)
                    )
                except (discord.Forbidden, discord.HTTPException):
                    pass

            if config and config["canal_logs"]:
                log_channel = guild.get_channel(config["canal_logs"])
                if isinstance(log_channel, discord.TextChannel):
                    log_lines = [
                        f"divisão · **{division_label}**",
                        f"resultado · **{status_text}**",
                        f"membro · <@{owner_id}> (`{owner_id}`)",
                        f"avaliador · {evaluator.mention} (`{evaluator.id}`)",
                        f"canal · `#{channel.name}`",
                    ]
                    if role_line:
                        log_lines.insert(2, role_line)
                    log_embed = discord.Embed(
                        title=f"teste de divisão · {status_text}",
                        description="\n".join(log_lines),
                        color=DEFAULT_COLOR,
                        timestamp=datetime.now(timezone.utc),
                    )
                    log_embed.set_footer(text=guild.name.lower())
                    try:
                        await log_channel.send(embed=log_embed)
                    except (discord.Forbidden, discord.HTTPException):
                        pass
        finally:
            _evaluating_channels.discard(channel.id)


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

        open_key = (guild.id, user.id)
        if open_key in _opening_tickets:
            await interaction.response.send_message(
                embed=discord.Embed(
                    description="seu ticket já está sendo aberto.",
                    color=DEFAULT_COLOR,
                ),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        _opening_tickets.add(open_key)
        try:
            await self._create_ticket(interaction, guild, user)
        finally:
            _opening_tickets.discard(open_key)

    async def _create_ticket(
        self,
        interaction: discord.Interaction,
        guild: discord.Guild,
        user: discord.Member,
    ) -> None:
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

        # Impede mais de um ticket de suporte simultâneo por usuário
        marker = f"user_id:{user.id}"
        for existing in guild.text_channels:
            if (
                existing.topic
                and existing.topic.startswith("ticket ·")
                and marker in existing.topic
            ):
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
        try:
            ticket_channel = await guild.create_text_channel(
                name=channel_name,
                category=category,
                topic=f"ticket · {marker}",
                overwrites=overwrites,
                reason=f"ticket aberto por {user}",
            )
        except discord.Forbidden:
            await interaction.followup.send(
                embed=discord.Embed(
                    description="não tenho permissão para criar o canal do ticket.",
                    color=DEFAULT_COLOR,
                ),
                ephemeral=True,
            )
            return
        except discord.HTTPException:
            await interaction.followup.send(
                embed=discord.Embed(
                    description="não foi possível criar o ticket. verifique se a categoria ainda tem espaço.",
                    color=DEFAULT_COLOR,
                ),
                ephemeral=True,
            )
            return

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

        try:
            await ticket_channel.send(
                content=f"{user.mention} {staff_mentions}",
                embed=welcome_embed,
                view=TicketCloseView(),
            )
        except (discord.Forbidden, discord.HTTPException):
            removed = False
            try:
                await ticket_channel.delete(reason="falha ao iniciar o ticket")
                removed = True
            except (discord.Forbidden, discord.HTTPException):
                pass
            if removed:
                description = "não consegui enviar a mensagem inicial. o canal foi removido."
            else:
                description = (
                    f"não consegui enviar a mensagem inicial em {ticket_channel.mention}."
                )
            await interaction.followup.send(
                embed=discord.Embed(description=description, color=DEFAULT_COLOR),
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            embed=discord.Embed(
                description=f"seu ticket foi criado em {ticket_channel.mention}.",
                color=DEFAULT_COLOR,
            ),
            ephemeral=True,
        )


class DivisionTicketOpenView(discord.ui.View):
    """View persistente do painel de testes de divisão (Aprendiz / Samurai)."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="teste · aprendiz",
        style=discord.ButtonStyle.secondary,
        custom_id="divisao:open:aprendiz",
    )
    async def open_aprendiz(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[DivisionTicketOpenView],
    ) -> None:
        await self._handle_click(interaction, "aprendiz")

    @discord.ui.button(
        label="teste · samurai",
        style=discord.ButtonStyle.secondary,
        custom_id="divisao:open:samurai",
    )
    async def open_samurai(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button[DivisionTicketOpenView],
    ) -> None:
        await self._handle_click(interaction, "samurai")

    async def _handle_click(
        self,
        interaction: discord.Interaction,
        division_key: str,
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

        open_key = (guild.id, user.id)
        if open_key in _opening_division_tickets:
            await interaction.response.send_message(
                embed=discord.Embed(
                    description="seu canal de teste já está sendo aberto.",
                    color=DEFAULT_COLOR,
                ),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        _opening_division_tickets.add(open_key)
        try:
            await self._create_division_ticket(interaction, guild, user, division_key)
        finally:
            _opening_division_tickets.discard(open_key)

    async def _create_division_ticket(
        self,
        interaction: discord.Interaction,
        guild: discord.Guild,
        user: discord.Member,
        division_key: str,
    ) -> None:
        bot = cast("KiraBot", interaction.client)
        config = await bot.db.get_guild_config(guild.id)
        staff_ids = await bot.db.get_staff_roles(guild.id)

        recrutados_id = config["cargo_recrutados"] if config else None
        aprendiz_id = config["cargo_aprendiz"] if config else None
        samurai_id = config["cargo_samurai"] if config else None

        if not recrutados_id or not aprendiz_id:
            await interaction.followup.send(
                embed=discord.Embed(
                    description="painel de divisões não configurado. peça a um administrador para usar `/setup-divisoes`.",
                    color=DEFAULT_COLOR,
                ),
                ephemeral=True,
            )
            return

        user_role_ids = {role.id for role in user.roles}

        if division_key == "aprendiz":
            division_label = "aprendiz"
            if samurai_id and samurai_id in user_role_ids:
                await interaction.followup.send(
                    embed=discord.Embed(
                        description=f"você já possui o cargo <@&{samurai_id}> e não precisa fazer o teste de **aprendiz**.",
                        color=DEFAULT_COLOR,
                    ),
                    ephemeral=True,
                )
                return
            if aprendiz_id in user_role_ids:
                await interaction.followup.send(
                    embed=discord.Embed(
                        description=f"você já possui o cargo <@&{aprendiz_id}>. use o botão **teste · samurai** para avançar.",
                        color=DEFAULT_COLOR,
                    ),
                    ephemeral=True,
                )
                return
            if recrutados_id not in user_role_ids:
                await interaction.followup.send(
                    embed=discord.Embed(
                        description=f"você precisa do cargo <@&{recrutados_id}> para fazer o teste de **aprendiz**.",
                        color=DEFAULT_COLOR,
                    ),
                    ephemeral=True,
                )
                return
        else:
            division_label = "samurai"
            if samurai_id and samurai_id in user_role_ids:
                await interaction.followup.send(
                    embed=discord.Embed(
                        description=f"você já possui o cargo <@&{samurai_id}>.",
                        color=DEFAULT_COLOR,
                    ),
                    ephemeral=True,
                )
                return
            if aprendiz_id not in user_role_ids:
                await interaction.followup.send(
                    embed=discord.Embed(
                        description=f"você precisa do cargo <@&{aprendiz_id}> para fazer o teste de **samurai**.",
                        color=DEFAULT_COLOR,
                    ),
                    ephemeral=True,
                )
                return

        if not staff_ids:
            await interaction.followup.send(
                embed=discord.Embed(
                    description="nenhum cargo de staff configurado. peça a um administrador para usar `/configurar` ou `/add-staff`.",
                    color=DEFAULT_COLOR,
                ),
                ephemeral=True,
            )
            return

        staff_roles = [
            role for r_id in staff_ids if (role := guild.get_role(r_id)) is not None
        ]
        if not staff_roles:
            await interaction.followup.send(
                embed=discord.Embed(
                    description="cargos de staff não encontrados. reconfigure com `/configurar` ou `/add-staff`.",
                    color=DEFAULT_COLOR,
                ),
                ephemeral=True,
            )
            return

        category: discord.CategoryChannel | None = None
        if config and config["categoria_tickets"]:
            configured_cat = guild.get_channel(config["categoria_tickets"])
            if isinstance(configured_cat, discord.CategoryChannel):
                category = configured_cat
        if category is None and isinstance(interaction.channel, discord.TextChannel):
            category = interaction.channel.category

        if category is None:
            await interaction.followup.send(
                embed=discord.Embed(
                    description="categoria de tickets não encontrada. peça a um administrador para configurar com `/configurar`.",
                    color=DEFAULT_COLOR,
                ),
                ephemeral=True,
            )
            return

        # Impede mais de um teste de divisão simultâneo por usuário
        marker = f"user_id:{user.id}"
        for existing in guild.text_channels:
            if (
                existing.topic
                and existing.topic.startswith("divisao:")
                and marker in existing.topic
            ):
                await interaction.followup.send(
                    embed=discord.Embed(
                        description=f"você já possui um teste de divisão aberto em {existing.mention}.",
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

        channel_name = f"teste-{division_key}-{_sanitize_username(user.name, user.id)}"
        try:
            ticket_channel = await guild.create_text_channel(
                name=channel_name,
                category=category,
                topic=f"divisao:{division_key} · {marker}",
                overwrites=overwrites,
                reason=f"teste de divisão ({division_label}) aberto por {user}",
            )
        except discord.Forbidden:
            await interaction.followup.send(
                embed=discord.Embed(
                    description="não tenho permissão para criar o canal do teste.",
                    color=DEFAULT_COLOR,
                ),
                ephemeral=True,
            )
            return
        except discord.HTTPException:
            await interaction.followup.send(
                embed=discord.Embed(
                    description="não foi possível criar o canal do teste. verifique se a categoria ainda tem espaço.",
                    color=DEFAULT_COLOR,
                ),
                ephemeral=True,
            )
            return

        staff_mentions = " ".join(f"<@&{r.id}>" for r in staff_roles)
        welcome_embed = discord.Embed(
            title=f"❖ teste de divisão · {division_label}",
            description=(
                f"olá {user.mention}, seu canal privado para o teste de **{division_label}** foi aberto.\n"
                f"aguarde um membro da equipe {staff_mentions} para iniciar sua avaliação."
            ),
            color=DEFAULT_COLOR,
        )
        welcome_embed.set_footer(
            text=f"{guild.name.lower()} · staff: use os botões abaixo para avaliar ou encerrar"
        )

        try:
            await ticket_channel.send(
                content=f"{user.mention} {staff_mentions}",
                embed=welcome_embed,
                view=DivisionTicketControlView(),
            )
        except (discord.Forbidden, discord.HTTPException):
            removed = False
            try:
                await ticket_channel.delete(reason="falha ao iniciar o teste de divisão")
                removed = True
            except (discord.Forbidden, discord.HTTPException):
                pass
            if removed:
                description = "não consegui enviar a mensagem inicial. o canal foi removido."
            else:
                description = (
                    f"não consegui enviar a mensagem inicial em {ticket_channel.mention}."
                )
            await interaction.followup.send(
                embed=discord.Embed(description=description, color=DEFAULT_COLOR),
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            embed=discord.Embed(
                description=f"seu canal de teste para **{division_label}** foi criado em {ticket_channel.mention}.",
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

        try:
            await interaction.channel.send(embed=panel_embed, view=TicketOpenView())
        except (discord.Forbidden, discord.HTTPException):
            await interaction.response.send_message(
                embed=discord.Embed(
                    description="não foi possível enviar o painel neste canal. verifique as permissões do bot.",
                    color=DEFAULT_COLOR,
                ),
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            embed=discord.Embed(
                description="painel de tickets enviado neste canal.",
                color=DEFAULT_COLOR,
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="setup-divisoes",
        description="configura os cargos e envia o painel de testes de divisão no canal atual.",
    )
    @app_commands.describe(
        cargo_recrutados="cargo Recrutados — requisito para o teste de Aprendiz (pode colar o ID)",
        cargo_aprendiz="cargo Aprendiz — ganho ao passar em Aprendiz e requisito para Samurai (pode colar o ID)",
        cargo_samurai="cargo Samurai — ganho ao passar no teste de Samurai (pode colar o ID)",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def setup_divisoes(
        self,
        interaction: discord.Interaction,
        cargo_recrutados: discord.Role,
        cargo_aprendiz: discord.Role,
        cargo_samurai: discord.Role,
    ) -> None:
        assert interaction.guild is not None
        if not isinstance(interaction.channel, discord.abc.Messageable):
            await interaction.response.send_message(
                embed=discord.Embed(
                    description="não foi possível enviar o painel neste canal.",
                    color=DEFAULT_COLOR,
                ),
                ephemeral=True,
            )
            return

        roles_to_check = [cargo_recrutados, cargo_aprendiz, cargo_samurai]

        if any(role.is_default() for role in roles_to_check):
            await interaction.response.send_message(
                embed=discord.Embed(
                    description="o cargo @everyone não pode ser usado nas divisões.",
                    color=DEFAULT_COLOR,
                ),
                ephemeral=True,
            )
            return

        if len({role.id for role in roles_to_check}) != len(roles_to_check):
            await interaction.response.send_message(
                embed=discord.Embed(
                    description="os cargos informados precisam ser diferentes entre si.",
                    color=DEFAULT_COLOR,
                ),
                ephemeral=True,
            )
            return

        await self.bot.db.set_division_roles(
            interaction.guild.id,
            cargo_recrutados=cargo_recrutados.id,
            cargo_aprendiz=cargo_aprendiz.id,
            cargo_samurai=cargo_samurai.id,
        )

        guild_name = interaction.guild.name.lower()
        panel_embed = discord.Embed(
            title="❖ testes de divisão",
            description="\n\n".join(
                [
                    "── ❖ ──── ❖ ──── ❖ ──",
                    (
                        "pronto para subir de patente?\n"
                        "escolha abaixo a divisão desejada para abrir um canal privado e realizar seu teste com a equipe."
                    ),
                    f"**aprendiz** ({cargo_aprendiz.mention})\nrequisito · possuir o cargo {cargo_recrutados.mention}",
                    f"**samurai** ({cargo_samurai.mention})\nrequisito · possuir o cargo {cargo_aprendiz.mention}",
                ]
            ),
            color=DEFAULT_COLOR,
        )
        panel_embed.set_footer(text=f"{guild_name} · testes de divisão")

        try:
            await interaction.channel.send(
                embed=panel_embed,
                view=DivisionTicketOpenView(),
            )
        except (discord.Forbidden, discord.HTTPException):
            await interaction.response.send_message(
                embed=discord.Embed(
                    description="não foi possível enviar o painel neste canal. verifique as permissões do bot.",
                    color=DEFAULT_COLOR,
                ),
                ephemeral=True,
            )
            return

        summary_lines = [
            "painel de divisões enviado neste canal.",
            f"recrutados · {cargo_recrutados.mention}",
            f"aprendiz · {cargo_aprendiz.mention}",
            f"samurai · {cargo_samurai.mention}",
        ]

        await interaction.response.send_message(
            embed=discord.Embed(
                description="\n".join(summary_lines),
                color=DEFAULT_COLOR,
            ),
            ephemeral=True,
        )


async def setup(bot: KiraBot) -> None:
    await bot.add_cog(Tickets(bot))
