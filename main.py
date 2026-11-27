from __future__ import annotations

import logging
import os
from pathlib import Path

import discord
from discord.ext import commands
from dotenv import load_dotenv

from cogs.tickets import TicketCloseView, TicketOpenView
from database.db import Database

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("kira_bot")


class KiraBot(commands.Bot):
    """Subclasse principal do bot com suporte a Slash Commands, Cogs e SQLite assíncrono."""

    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.members = True

        super().__init__(command_prefix="!", intents=intents)
        self.db = Database("bot.db")

    async def setup_hook(self) -> None:
        # 1. Inicializa o banco de dados SQLite
        await self.db.init_db()
        logger.info("Banco de dados inicializado com sucesso.")

        # 2. Carrega todos os arquivos .py dentro de cogs/
        cogs_dir = Path(__file__).parent / "cogs"
        if cogs_dir.exists():
            for file in sorted(cogs_dir.glob("*.py")):
                if file.name.startswith("_"):
                    continue
                extension = f"cogs.{file.stem}"
                try:
                    await self.load_extension(extension)
                    logger.info("Cog carregado: %s", extension)
                except Exception:
                    logger.exception("Falha ao carregar cog: %s", extension)

        # 3. Registra Views persistentes (timeout=None)
        self.add_view(TicketOpenView())
        self.add_view(TicketCloseView())
        logger.info("Views persistentes registradas com sucesso.")

        # 4. Sincroniza a árvore de Slash Commands
        synced = await self.tree.sync()
        logger.info("Árvore de comandos sincronizada (%d comando(s)).", len(synced))

    async def on_ready(self) -> None:
        if self.user:
            logger.info("Bot conectado como %s (ID: %s)", self.user, self.user.id)

    async def close(self) -> None:
        await self.db.close()
        await super().close()


def main() -> None:
    load_dotenv()
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError(
            "DISCORD_TOKEN não encontrado. Crie um arquivo .env baseado em .env.example."
        )

    bot = KiraBot()
    bot.run(token, log_handler=None)


if __name__ == "__main__":
    main()
