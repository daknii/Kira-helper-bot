from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

if TYPE_CHECKING:
    from main import KiraBot

DEFAULT_COLOR = 0x2B2D31


class Moderation(commands.Cog):
    """Sistema completo de moderação com estética minimalista e logs automáticos."""

    def __init__(self, bot: KiraBot) -> None:
        self.bot = bot

    async def _has_permission(
        self,
        interaction: discord.Interaction,
        native_perm: str,
    ) -> bool:
        """Verifica se o usuário possui a permissão nativa do Discord ou o cargo staff."""
        guild = interaction.guild
        member = interaction.user
        if guild is None or not isinstance(member, discord.Member):
            return False

        perms = member.guild_permissions
        if perms.administrator or getattr(perms, native_perm, False):
            return True

        staff_ids = await self.bot.db.get_staff_roles(guild.id)
        if staff_ids and any(role.id in staff_ids for role in member.roles):
            return True

        return False

    def _check_hierarchy(
        self,
        guild: discord.Guild,
        moderator: discord.Member,
        target: discord.Member,
    ) -> str | None:
        """Valida hierarquia de cargos entre moderador, bot e alvo."""
        if target.id == moderator.id:
            return "você não pode aplicar esta ação em si mesmo."
        if guild.me and target.id == guild.me.id:
            return "não posso aplicar esta ação em mim mesmo."
        if target.id == guild.owner_id:
            return "não é possível punir o dono do servidor."
        if moderator.id != guild.owner_id and target.top_role >= moderator.top_role:
            return "você não pode punir alguém com cargo igual ou superior ao seu."
        if guild.me and target.top_role >= guild.me.top_role:
            return "não tenho hierarquia suficiente para punir este membro."
        return None

    async def _send_log(self, guild: discord.Guild, embed: discord.Embed) -> None:
        """Envia o embed de auditoria no canal_logs configurado em guild_config."""
        config = await self.bot.db.get_guild_config(guild.id)
        if not config or not config["canal_logs"]:
            return

        log_channel = guild.get_channel(config["canal_logs"])
        if not isinstance(log_channel, discord.TextChannel):
            return

        embed.timestamp = datetime.now(timezone.utc)
        if not embed.footer.text:
            embed.set_footer(text=guild.name.lower())

        try:
            await log_channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            pass

    async def _send_punishment(
        self,
        guild: discord.Guild,
        *,
        target_text: str,
        action: str,
        reason: str,
        moderator: discord.abc.User,
        duration: str | None = None,
    ) -> None:
        """Envia um embed resumido no canal público de punições."""
        config = await self.bot.db.get_guild_config(guild.id)
        if not config or not config["canal_punicoes"]:
            return

        channel = guild.get_channel(config["canal_punicoes"])
        if not isinstance(channel, discord.TextChannel):
            return

        lines = [
            f"membro · {target_text}",
            f"punição · `{action}`",
        ]
        if duration:
            lines.append(f"duração · `{duration}`")
        lines.extend(
            [
                f"motivo · {reason}",
                f"moderador · {moderator.mention}",
            ]
        )

        embed = discord.Embed(
            title="registro de punição",
            description="\n".join(lines),
            color=DEFAULT_COLOR,
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text=guild.name.lower())

        try:
            await channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            pass

    @staticmethod
    def _error_embed(message: str) -> discord.Embed:
        return discord.Embed(description=message, color=DEFAULT_COLOR)

    @app_commands.command(
        name="warn",
        description="registra uma advertência para um membro.",
    )
    @app_commands.describe(
        membro="membro que receberá a advertência",
        motivo="motivo da advertência",
    )
    @app_commands.guild_only()
    async def warn(
        self,
        interaction: discord.Interaction,
        membro: discord.Member,
        motivo: app_commands.Range[str, 1, 400],
    ) -> None:
        assert interaction.guild is not None
        assert isinstance(interaction.user, discord.Member)

        if not await self._has_permission(interaction, "moderate_members"):
            await interaction.response.send_message(
                embed=self._error_embed("você não tem permissão para advertir membros."),
                ephemeral=True,
            )
            return

        if membro.bot:
            await interaction.response.send_message(
                embed=self._error_embed("não é possível advertir bots."),
                ephemeral=True,
            )
            return

        hierarchy_error = self._check_hierarchy(interaction.guild, interaction.user, membro)
        if hierarchy_error:
            await interaction.response.send_message(
                embed=self._error_embed(hierarchy_error),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        warn_id, total_warns = await self.bot.db.add_warn(
            interaction.guild.id,
            membro.id,
            interaction.user.id,
            motivo,
        )

        dm_sent = True
        dm_embed = discord.Embed(
            title=f"advertência · {interaction.guild.name.lower()}",
            description="\n".join(
                [
                    f"motivo · {motivo}",
                    f"total de advertências · `{total_warns}`",
                ]
            ),
            color=DEFAULT_COLOR,
        )
        dm_embed.set_footer(text=f"warn #{warn_id}")
        try:
            await membro.send(embed=dm_embed)
        except (discord.Forbidden, discord.HTTPException):
            dm_sent = False

        dm_note = "aviso enviado na dm" if dm_sent else "dm fechada (não notificado)"
        confirm_embed = discord.Embed(
            title="advertência registrada",
            description="\n".join(
                [
                    f"membro · {membro.mention}",
                    f"motivo · {motivo}",
                    f"warns ativos · `{total_warns}` · id `#{warn_id}`",
                ]
            ),
            color=DEFAULT_COLOR,
        )
        confirm_embed.set_footer(text=dm_note)
        await interaction.followup.send(embed=confirm_embed, ephemeral=True)

        log_embed = discord.Embed(
            title="log · warn",
            description="\n".join(
                [
                    f"membro · {membro.mention} (`{membro.id}`)",
                    f"moderador · {interaction.user.mention} (`{interaction.user.id}`)",
                    f"motivo · {motivo}",
                    f"id · `#{warn_id}` · total · `{total_warns}`",
                    f"dm · {dm_note}",
                ]
            ),
            color=DEFAULT_COLOR,
        )
        await self._send_log(interaction.guild, log_embed)
        await self._send_punishment(
            interaction.guild,
            target_text=f"{membro.mention} (`{membro.id}`)",
            action=f"warn #{warn_id} ({total_warns} ativo(s))",
            reason=motivo,
            moderator=interaction.user,
        )

    @app_commands.command(
        name="warns",
        description="lista todas as advertências registradas de um membro.",
    )
    @app_commands.describe(membro="membro para consultar o histórico")
    @app_commands.guild_only()
    async def warns(
        self,
        interaction: discord.Interaction,
        membro: discord.Member,
    ) -> None:
        assert interaction.guild is not None

        if not await self._has_permission(interaction, "moderate_members"):
            await interaction.response.send_message(
                embed=self._error_embed("você não tem permissão para consultar advertências."),
                ephemeral=True,
            )
            return

        rows = await self.bot.db.get_warns(interaction.guild.id, membro.id)
        if not rows:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title=f"advertências · {membro.display_name.lower()}",
                    description="nenhuma advertência registrada.",
                    color=DEFAULT_COLOR,
                ),
                ephemeral=True,
            )
            return

        shown = rows[:10]
        entries: list[str] = []
        for row in shown:
            reason = str(row["reason"]).replace("\n", " ")
            if len(reason) > 180:
                reason = reason[:177] + "..."
            created_at = str(row["created_at"])[:16]
            entries.append(
                f"`#{row['id']}` · {reason}\n"
                f"moderador · <@{row['moderator_id']}> · data · `{created_at}`"
            )

        description = "\n\n".join(entries)
        if len(rows) > len(shown):
            description += f"\n\nmostrando as `{len(shown)}` mais recentes de `{len(rows)}`."

        embed = discord.Embed(
            title=f"advertências · {membro.display_name.lower()}",
            description=description,
            color=DEFAULT_COLOR,
        )
        embed.set_footer(text=f"total · {len(rows)} advertência(s)")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="unwarn",
        description="remove uma advertência específica pelo id.",
    )
    @app_commands.describe(id_warn="id numérico da advertência")
    @app_commands.guild_only()
    async def unwarn(
        self,
        interaction: discord.Interaction,
        id_warn: int,
    ) -> None:
        assert interaction.guild is not None

        if not await self._has_permission(interaction, "moderate_members"):
            await interaction.response.send_message(
                embed=self._error_embed("você não tem permissão para remover advertências."),
                ephemeral=True,
            )
            return

        removed = await self.bot.db.delete_warn(interaction.guild.id, id_warn)
        if removed is None:
            await interaction.response.send_message(
                embed=self._error_embed(f"advertência `#{id_warn}` não encontrada neste servidor."),
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            embed=discord.Embed(
                title="advertência removida",
                description="\n".join(
                    [
                        f"id · `#{removed['id']}`",
                        f"membro · <@{removed['user_id']}>",
                        f"motivo original · {removed['reason']}",
                    ]
                ),
                color=DEFAULT_COLOR,
            ),
            ephemeral=True,
        )

        log_embed = discord.Embed(
            title="log · unwarn",
            description="\n".join(
                [
                    f"id · `#{removed['id']}`",
                    f"membro · <@{removed['user_id']}> (`{removed['user_id']}`)",
                    f"removido por · {interaction.user.mention} (`{interaction.user.id}`)",
                    f"motivo original · {removed['reason']}",
                ]
            ),
            color=DEFAULT_COLOR,
        )
        await self._send_log(interaction.guild, log_embed)

    @app_commands.command(
        name="timeout",
        description="aplica castigo temporário (timeout) em um membro.",
    )
    @app_commands.describe(
        membro="membro que receberá o timeout",
        minutos="duração do castigo em minutos (1 a 40320)",
        motivo="motivo do castigo",
    )
    @app_commands.guild_only()
    async def timeout(
        self,
        interaction: discord.Interaction,
        membro: discord.Member,
        minutos: app_commands.Range[int, 1, 40320],
        motivo: app_commands.Range[str, 1, 400] = "sem motivo informado",
    ) -> None:
        assert interaction.guild is not None
        assert isinstance(interaction.user, discord.Member)

        if not await self._has_permission(interaction, "moderate_members"):
            await interaction.response.send_message(
                embed=self._error_embed("você não tem permissão para aplicar timeout."),
                ephemeral=True,
            )
            return

        hierarchy_error = self._check_hierarchy(interaction.guild, interaction.user, membro)
        if hierarchy_error:
            await interaction.response.send_message(
                embed=self._error_embed(hierarchy_error),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        until = datetime.now(timezone.utc) + timedelta(minutes=minutos)
        try:
            await membro.timeout(until, reason=f"{interaction.user}: {motivo}")
        except discord.Forbidden:
            await interaction.followup.send(
                embed=self._error_embed("não tenho permissão para aplicar timeout neste membro."),
                ephemeral=True,
            )
            return
        except discord.HTTPException:
            await interaction.followup.send(
                embed=self._error_embed("não foi possível aplicar o timeout neste membro."),
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="timeout aplicado",
            description="\n".join(
                [
                    f"membro · {membro.mention}",
                    f"duração · `{minutos} minuto(s)`",
                    f"motivo · {motivo}",
                ]
            ),
            color=DEFAULT_COLOR,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

        log_embed = discord.Embed(
            title="log · timeout",
            description="\n".join(
                [
                    f"membro · {membro.mention} (`{membro.id}`)",
                    f"moderador · {interaction.user.mention} (`{interaction.user.id}`)",
                    f"duração · `{minutos} minuto(s)`",
                    f"motivo · {motivo}",
                ]
            ),
            color=DEFAULT_COLOR,
        )
        await self._send_log(interaction.guild, log_embed)
        await self._send_punishment(
            interaction.guild,
            target_text=f"{membro.mention} (`{membro.id}`)",
            action="timeout",
            duration=f"{minutos} minuto(s)",
            reason=motivo,
            moderator=interaction.user,
        )

    @app_commands.command(
        name="kick",
        description="expulsa um membro do servidor.",
    )
    @app_commands.describe(
        membro="membro a ser expulso",
        motivo="motivo da expulsão",
    )
    @app_commands.guild_only()
    async def kick(
        self,
        interaction: discord.Interaction,
        membro: discord.Member,
        motivo: app_commands.Range[str, 1, 400] = "sem motivo informado",
    ) -> None:
        assert interaction.guild is not None
        assert isinstance(interaction.user, discord.Member)

        if not await self._has_permission(interaction, "kick_members"):
            await interaction.response.send_message(
                embed=self._error_embed("você não tem permissão para expulsar membros."),
                ephemeral=True,
            )
            return

        hierarchy_error = self._check_hierarchy(interaction.guild, interaction.user, membro)
        if hierarchy_error:
            await interaction.response.send_message(
                embed=self._error_embed(hierarchy_error),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            await membro.kick(reason=f"{interaction.user}: {motivo}")
        except discord.Forbidden:
            await interaction.followup.send(
                embed=self._error_embed("não tenho permissão para expulsar este membro."),
                ephemeral=True,
            )
            return
        except discord.HTTPException:
            await interaction.followup.send(
                embed=self._error_embed("não foi possível expulsar este membro."),
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="membro expulso",
            description="\n".join(
                [
                    f"membro · `{membro}` (`{membro.id}`)",
                    f"motivo · {motivo}",
                ]
            ),
            color=DEFAULT_COLOR,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

        log_embed = discord.Embed(
            title="log · kick",
            description="\n".join(
                [
                    f"membro · `{membro}` (`{membro.id}`)",
                    f"moderador · {interaction.user.mention} (`{interaction.user.id}`)",
                    f"motivo · {motivo}",
                ]
            ),
            color=DEFAULT_COLOR,
        )
        await self._send_log(interaction.guild, log_embed)
        await self._send_punishment(
            interaction.guild,
            target_text=f"`{membro}` (`{membro.id}`)",
            action="kick",
            reason=motivo,
            moderator=interaction.user,
        )

    @app_commands.command(
        name="ban",
        description="bane um membro do servidor.",
    )
    @app_commands.describe(
        membro="membro a ser banido",
        motivo="motivo do banimento",
        deletar_mensagens_dias="dias de histórico de mensagens para apagar (0 a 7)",
    )
    @app_commands.guild_only()
    async def ban(
        self,
        interaction: discord.Interaction,
        membro: discord.Member,
        motivo: app_commands.Range[str, 1, 400] = "sem motivo informado",
        deletar_mensagens_dias: app_commands.Range[int, 0, 7] = 0,
    ) -> None:
        assert interaction.guild is not None
        assert isinstance(interaction.user, discord.Member)

        if not await self._has_permission(interaction, "ban_members"):
            await interaction.response.send_message(
                embed=self._error_embed("você não tem permissão para banir membros."),
                ephemeral=True,
            )
            return

        hierarchy_error = self._check_hierarchy(interaction.guild, interaction.user, membro)
        if hierarchy_error:
            await interaction.response.send_message(
                embed=self._error_embed(hierarchy_error),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            await membro.ban(
                reason=f"{interaction.user}: {motivo}",
                delete_message_seconds=deletar_mensagens_dias * 86400,
            )
        except discord.Forbidden:
            await interaction.followup.send(
                embed=self._error_embed("não tenho permissão para banir este membro."),
                ephemeral=True,
            )
            return
        except discord.HTTPException:
            await interaction.followup.send(
                embed=self._error_embed("não foi possível banir este membro."),
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="membro banido",
            description="\n".join(
                [
                    f"membro · `{membro}` (`{membro.id}`)",
                    f"motivo · {motivo}",
                    f"mensagens apagadas · `{deletar_mensagens_dias} dia(s)`",
                ]
            ),
            color=DEFAULT_COLOR,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

        log_embed = discord.Embed(
            title="log · ban",
            description="\n".join(
                [
                    f"membro · `{membro}` (`{membro.id}`)",
                    f"moderador · {interaction.user.mention} (`{interaction.user.id}`)",
                    f"motivo · {motivo}",
                    f"mensagens apagadas · `{deletar_mensagens_dias} dia(s)`",
                ]
            ),
            color=DEFAULT_COLOR,
        )
        await self._send_log(interaction.guild, log_embed)
        await self._send_punishment(
            interaction.guild,
            target_text=f"`{membro}` (`{membro.id}`)",
            action="ban",
            reason=motivo,
            moderator=interaction.user,
        )

    @app_commands.command(
        name="clear",
        description="limpa entre 1 e 100 mensagens do canal atual.",
    )
    @app_commands.describe(quantidade="quantidade de mensagens a apagar (1 a 100)")
    @app_commands.guild_only()
    async def clear(
        self,
        interaction: discord.Interaction,
        quantidade: app_commands.Range[int, 1, 100],
    ) -> None:
        assert interaction.guild is not None

        if not await self._has_permission(interaction, "manage_messages"):
            await interaction.response.send_message(
                embed=self._error_embed("você não tem permissão para limpar mensagens."),
                ephemeral=True,
            )
            return

        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                embed=self._error_embed("este comando só pode ser usado em canais de texto."),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            deleted = await channel.purge(limit=quantidade)
        except (discord.Forbidden, discord.HTTPException):
            await interaction.followup.send(
                embed=self._error_embed("não foi possível apagar as mensagens neste canal."),
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            embed=discord.Embed(
                description=f"`{len(deleted)}` mensagem(ns) removida(s) em {channel.mention}.",
                color=DEFAULT_COLOR,
            ),
            ephemeral=True,
        )

        log_embed = discord.Embed(
            title="log · clear",
            description="\n".join(
                [
                    f"canal · {channel.mention} (`{channel.id}`)",
                    f"moderador · {interaction.user.mention} (`{interaction.user.id}`)",
                    f"mensagens removidas · `{len(deleted)}`",
                ]
            ),
            color=DEFAULT_COLOR,
        )
        await self._send_log(interaction.guild, log_embed)


async def setup(bot: KiraBot) -> None:
    await bot.add_cog(Moderation(bot))
