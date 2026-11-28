from __future__ import annotations

import re
from datetime import datetime
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

if TYPE_CHECKING:
    from main import KiraBot

DEFAULT_COLOR = 0x2B2D31
HEX_COLOR_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")


def parse_hex_color(value: str | None) -> int | None:
    """Converte uma cor hexadecimal em inteiro, ou retorna DEFAULT_COLOR se vazio."""
    if not value or not value.strip():
        return DEFAULT_COLOR
    match = HEX_COLOR_RE.match(value.strip())
    if not match:
        return None
    return int(match.group(1), 16)


class EmbedBuilderModal(discord.ui.Modal, title="criar embed"):
    """Modal minimalista para envio de embeds customizados."""

    titulo = discord.ui.TextInput(
        label="título",
        placeholder="título do embed",
        max_length=256,
        required=True,
    )
    descricao = discord.ui.TextInput(
        label="descrição",
        placeholder="conteúdo do embed...",
        style=discord.TextStyle.paragraph,
        max_length=4000,
        required=True,
    )
    cor_hex = discord.ui.TextInput(
        label="cor hex (opcional)",
        placeholder="#2b2d31",
        max_length=7,
        required=False,
    )
    imagem_url = discord.ui.TextInput(
        label="url de imagem (opcional)",
        placeholder="https://exemplo.com/imagem.png",
        max_length=512,
        required=False,
    )
    rodape = discord.ui.TextInput(
        label="footer (opcional)",
        placeholder="padrão: nome do servidor",
        max_length=2048,
        required=False,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        color_value = parse_hex_color(self.cor_hex.value)
        if color_value is None:
            await interaction.response.send_message(
                embed=discord.Embed(
                    description="cor hex inválida. use o formato `#2b2d31`.",
                    color=DEFAULT_COLOR,
                ),
                ephemeral=True,
            )
            return

        title = self.titulo.value.strip()
        description = self.descricao.value.strip()
        if not title or not description:
            await interaction.response.send_message(
                embed=discord.Embed(
                    description="título e descrição não podem ficar vazios.",
                    color=DEFAULT_COLOR,
                ),
                ephemeral=True,
            )
            return

        image = self.imagem_url.value.strip()
        if image and not (image.startswith("http://") or image.startswith("https://")):
            await interaction.response.send_message(
                embed=discord.Embed(
                    description="url de imagem inválida. informe um link iniciado por `http://` ou `https://`.",
                    color=DEFAULT_COLOR,
                ),
                ephemeral=True,
            )
            return

        if not isinstance(interaction.channel, discord.abc.Messageable):
            await interaction.response.send_message(
                embed=discord.Embed(
                    description="não foi possível enviar o embed neste canal.",
                    color=DEFAULT_COLOR,
                ),
                ephemeral=True,
            )
            return

        custom_embed = discord.Embed(
            title=title,
            description=description,
            color=color_value,
        )
        if image:
            custom_embed.set_image(url=image)

        footer_text = self.rodape.value.strip()
        if not footer_text and interaction.guild:
            footer_text = interaction.guild.name.lower()
        if footer_text:
            custom_embed.set_footer(text=footer_text)

        try:
            await interaction.channel.send(embed=custom_embed)
        except (discord.Forbidden, discord.HTTPException):
            await interaction.response.send_message(
                embed=discord.Embed(
                    description="não foi possível enviar o embed. confira a url da imagem e as permissões do bot neste canal.",
                    color=DEFAULT_COLOR,
                ),
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            embed=discord.Embed(
                description="embed enviado neste canal.",
                color=DEFAULT_COLOR,
            ),
            ephemeral=True,
        )


class Setup(commands.Cog):
    """Comandos administrativos de configuração e utilidades visuais."""

    def __init__(self, bot: KiraBot) -> None:
        self.bot = bot

    @app_commands.command(
        name="configurar",
        description="configura canais e cargos da equipe para o servidor.",
    )
    @app_commands.describe(
        canal_logs="canal de logs da staff e transcripts de tickets",
        categoria_tickets="categoria onde os tickets serão criados",
        cargo_staff_1="adiciona um cargo de staff (ex: alto-escalão)",
        cargo_staff_2="adiciona outro cargo de staff (opcional)",
        cargo_staff_3="adiciona outro cargo de staff (opcional)",
        canal_entrada="canal de boas-vindas",
        canal_saida="canal de avisos de saída",
        canal_punicoes="mural público de punições",
        canal_armadilha="canal armadilha (quem enviar mensagem é banido automaticamente)",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def configurar(
        self,
        interaction: discord.Interaction,
        canal_logs: discord.TextChannel | None = None,
        categoria_tickets: discord.CategoryChannel | None = None,
        cargo_staff_1: discord.Role | None = None,
        cargo_staff_2: discord.Role | None = None,
        cargo_staff_3: discord.Role | None = None,
        canal_entrada: discord.TextChannel | None = None,
        canal_saida: discord.TextChannel | None = None,
        canal_punicoes: discord.TextChannel | None = None,
        canal_armadilha: discord.TextChannel | None = None,
    ) -> None:
        assert interaction.guild is not None

        provided_roles = [
            role
            for role in (cargo_staff_1, cargo_staff_2, cargo_staff_3)
            if role is not None
        ]
        if any(role.is_default() for role in provided_roles):
            await interaction.response.send_message(
                embed=discord.Embed(
                    description="o cargo @everyone não pode ser usado como staff.",
                    color=DEFAULT_COLOR,
                ),
                ephemeral=True,
            )
            return

        staff_role_ids: list[int] | None = None
        if provided_roles:
            current_roles = await self.bot.db.get_staff_roles(interaction.guild.id)
            staff_role_ids = list(
                dict.fromkeys([*current_roles, *(role.id for role in provided_roles)])
            )

        updated = any(
            param is not None
            for param in (
                canal_logs,
                categoria_tickets,
                staff_role_ids,
                canal_entrada,
                canal_saida,
                canal_punicoes,
                canal_armadilha,
            )
        )

        if updated:
            config = await self.bot.db.upsert_guild_config(
                interaction.guild.id,
                canal_logs=canal_logs.id if canal_logs else None,
                categoria_tickets=categoria_tickets.id if categoria_tickets else None,
                staff_role_ids=staff_role_ids,
                canal_entrada=canal_entrada.id if canal_entrada else None,
                canal_saida=canal_saida.id if canal_saida else None,
                canal_punicoes=canal_punicoes.id if canal_punicoes else None,
                canal_armadilha=canal_armadilha.id if canal_armadilha else None,
            )
            title = "configuração salva"
        else:
            config = await self.bot.db.get_guild_config(interaction.guild.id)
            title = "configuração atual"

        def fmt_channel(channel_id: int | None) -> str:
            return f"<#{channel_id}>" if channel_id else "não definido"

        saved_staff = (
            self.bot.db.parse_staff_roles(config["staff_role_ids"]) if config else []
        )
        staff_text = (
            " ".join(f"<@&{role_id}>" for role_id in saved_staff)
            if saved_staff
            else "não definido"
        )

        lines = [
            f"canal de logs · {fmt_channel(config['canal_logs'] if config else None)}",
            f"categoria de tickets · {fmt_channel(config['categoria_tickets'] if config else None)}",
            f"cargos staff · {staff_text}",
            f"canal de entrada · {fmt_channel(config['canal_entrada'] if config else None)}",
            f"canal de saída · {fmt_channel(config['canal_saida'] if config else None)}",
            f"canal de punições · {fmt_channel(config['canal_punicoes'] if config else None)}",
            f"canal armadilha · {fmt_channel(config['canal_armadilha'] if config else None)}",
        ]

        embed = discord.Embed(
            title=title,
            description="\n".join(lines),
            color=DEFAULT_COLOR,
        )
        embed.set_footer(text=interaction.guild.name.lower())

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="add-staff",
        description="adiciona um cargo à lista de cargos de staff do servidor.",
    )
    @app_commands.describe(cargo="cargo que receberá acesso de staff")
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def add_staff(
        self,
        interaction: discord.Interaction,
        cargo: discord.Role,
    ) -> None:
        assert interaction.guild is not None

        if cargo.is_default():
            await interaction.response.send_message(
                embed=discord.Embed(
                    description="o cargo @everyone não pode ser usado como staff.",
                    color=DEFAULT_COLOR,
                ),
                ephemeral=True,
            )
            return

        roles = await self.bot.db.add_staff_role(interaction.guild.id, cargo.id)
        staff_text = " ".join(f"<@&{role_id}>" for role_id in roles)

        embed = discord.Embed(
            title="cargo staff adicionado",
            description="\n".join(
                [
                    f"adicionado · {cargo.mention}",
                    f"cargos ativos · {staff_text}",
                ]
            ),
            color=DEFAULT_COLOR,
        )
        embed.set_footer(text=interaction.guild.name.lower())
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="remove-staff",
        description="remove um cargo da lista de cargos de staff do servidor.",
    )
    @app_commands.describe(cargo="cargo a ser removido da lista de staff")
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def remove_staff(
        self,
        interaction: discord.Interaction,
        cargo: discord.Role,
    ) -> None:
        assert interaction.guild is not None

        removed, roles = await self.bot.db.remove_staff_role(
            interaction.guild.id, cargo.id
        )
        staff_text = (
            " ".join(f"<@&{role_id}>" for role_id in roles)
            if roles
            else "nenhum cargo configurado"
        )

        if not removed:
            embed = discord.Embed(
                description=f"{cargo.mention} não estava na lista de staff.\ncargos ativos · {staff_text}",
                color=DEFAULT_COLOR,
            )
        else:
            embed = discord.Embed(
                title="cargo staff removido",
                description="\n".join(
                    [
                        f"removido · {cargo.mention}",
                        f"cargos ativos · {staff_text}",
                    ]
                ),
                color=DEFAULT_COLOR,
            )
        embed.set_footer(text=interaction.guild.name.lower())

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="setup-regras",
        description="envia o painel minimalista de regras no canal atual.",
    )
    @app_commands.describe(
        canal_tickets="canal de tickets mencionado na regra 11 (opcional)",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def setup_regras(
        self,
        interaction: discord.Interaction,
        canal_tickets: discord.TextChannel | None = None,
    ) -> None:
        if not isinstance(interaction.channel, discord.abc.Messageable):
            await interaction.response.send_message(
                embed=discord.Embed(
                    description="não foi possível enviar as regras neste canal.",
                    color=DEFAULT_COLOR,
                ),
                ephemeral=True,
            )
            return

        if canal_tickets:
            support_line = (
                f"**11. suporte**\nprecisa de ajuda? abra um ticket em {canal_tickets.mention} "
                "ou chame a equipe superior."
            )
        else:
            support_line = (
                "**11. suporte**\nprecisa de ajuda? abra um ticket no canal de suporte "
                "ou chame a equipe superior."
            )

        rules_text = "\n\n".join(
            [
                "── ❖ ──── ❖ ──── ❖ ──",
                "**01. respeito mútuo**\ntrate todos os membros com respeito, sem ofensas ou provocações.",
                "**02. sem preconceito**\ndiscurso de ódio, assédio e preconceito não serão tolerados.",
                "**03. canais corretos**\nutilize cada canal apenas para o seu tema correspondente.",
                "**04. sem spam e flood**\nevite poluir o chat com mensagens repetitivas ou marcações.",
                "**05. conteúdo limpo**\nproibido qualquer envio de conteúdo nsfw, gore ou chocante.",
                "**06. sem drama**\nresolva conflitos pessoais no privado, mantendo a ordem no chat.",
                "**07. nome e perfil**\nmantenha um apelido legível, sem caracteres invisíveis.",
                "**08. sem divulgação**\nproibido divulgar links ou outros servidores sem permissão.",
                "**09. hierarquia e ordem**\nrespeite as orientações e decisões da moderação.",
                "**10. bom senso**\nmantenha o ambiente agradável, divertido e respeitoso.",
                support_line,
            ]
        )

        embed = discord.Embed(
            title="❖꙳ regras do server ❖꙳",
            description=rules_text,
            color=DEFAULT_COLOR,
        )
        embed.set_footer(
            text="ao permanecer no servidor, você concorda com todas as regras acima."
        )

        try:
            await interaction.channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            await interaction.response.send_message(
                embed=discord.Embed(
                    description="não foi possível enviar as regras neste canal. verifique as permissões do bot.",
                    color=DEFAULT_COLOR,
                ),
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            embed=discord.Embed(
                description="painel de regras enviado neste canal.",
                color=DEFAULT_COLOR,
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="setup-armadilha",
        description="configura o canal atual como armadilha anti-spam e envia o aviso.",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def setup_armadilha(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                embed=discord.Embed(
                    description="este comando só pode ser usado em um canal de texto.",
                    color=DEFAULT_COLOR,
                ),
                ephemeral=True,
            )
            return

        await self.bot.db.upsert_guild_config(
            interaction.guild.id,
            canal_armadilha=channel.id,
        )

        warning_embed = discord.Embed(
            title="⚠ não envie mensagens aqui",
            description=(
                "este canal é uma **armadilha anti-spam**.\n"
                "qualquer membro que enviar uma mensagem neste canal será **banido automaticamente**.\n\n"
                "administradores e cargos da staff são imunes."
            ),
            color=DEFAULT_COLOR,
        )
        warning_embed.set_footer(text=interaction.guild.name.lower())

        try:
            await channel.send(embed=warning_embed)
        except (discord.Forbidden, discord.HTTPException):
            await interaction.response.send_message(
                embed=discord.Embed(
                    description=(
                        f"{channel.mention} foi salvo como canal armadilha, mas não foi possível "
                        "enviar o aviso aqui. verifique as permissões do bot."
                    ),
                    color=DEFAULT_COLOR,
                ),
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            embed=discord.Embed(
                description=(
                    f"{channel.mention} configurado como **canal armadilha**.\n"
                    "qualquer membro comum que enviar mensagem aqui será banido automaticamente."
                ),
                color=DEFAULT_COLOR,
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="desativar-armadilha",
        description="desativa o canal armadilha configurado no servidor.",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def desativar_armadilha(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        config = await self.bot.db.get_guild_config(interaction.guild.id)
        if not config or not config["canal_armadilha"]:
            await interaction.response.send_message(
                embed=discord.Embed(
                    description="nenhum canal armadilha está ativo no momento.",
                    color=DEFAULT_COLOR,
                ),
                ephemeral=True,
            )
            return

        previous_id = config["canal_armadilha"]
        await self.bot.db.clear_trap_channel(interaction.guild.id)
        await interaction.response.send_message(
            embed=discord.Embed(
                description=f"canal armadilha (<#{previous_id}>) desativado.",
                color=DEFAULT_COLOR,
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="embed",
        description="abre um formulário para enviar um embed customizado no canal atual.",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def embed(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(EmbedBuilderModal())

    @app_commands.command(
        name="backup-db",
        description="exporta um backup do banco de dados SQLite na sua DM (apenas dono do bot).",
    )
    async def backup_db(self, interaction: discord.Interaction) -> None:
        if not await self.bot.is_owner(interaction.user):
            await interaction.response.send_message(
                embed=discord.Embed(
                    description="acesso negado. apenas o dono da aplicação pode usar este comando.",
                    color=DEFAULT_COLOR,
                ),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        if self.bot.db.conn is not None:
            await self.bot.db.conn.commit()
            await self.bot.db.conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")

        db_path = self.bot.db.db_path
        if not db_path.exists():
            await interaction.followup.send(
                embed=discord.Embed(
                    description=f"arquivo `{db_path.name}` não encontrado.",
                    color=DEFAULT_COLOR,
                ),
                ephemeral=True,
            )
            return

        now = datetime.now()
        timestamp_str = now.strftime("%d/%m/%Y às %H:%M:%S")

        try:
            await interaction.user.send(
                content=f"backup do banco de dados (`bot.db`) gerado em **{timestamp_str}**.",
                file=discord.File(str(db_path), filename="bot.db"),
            )
        except discord.Forbidden:
            await interaction.followup.send(
                embed=discord.Embed(
                    description=(
                        "não foi possível enviar o backup no privado. "
                        "verifique se suas mensagens diretas (DM) estão abertas."
                    ),
                    color=DEFAULT_COLOR,
                ),
                ephemeral=True,
            )
            return
        except discord.HTTPException:
            await interaction.followup.send(
                embed=discord.Embed(
                    description="ocorreu um erro ao enviar o arquivo de backup pelo Discord.",
                    color=DEFAULT_COLOR,
                ),
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            embed=discord.Embed(
                description=f"backup de `{db_path.name}` enviado no seu privado ({timestamp_str}).",
                color=DEFAULT_COLOR,
            ),
            ephemeral=True,
        )


async def setup(bot: KiraBot) -> None:
    await bot.add_cog(Setup(bot))
