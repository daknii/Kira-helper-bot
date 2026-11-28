from __future__ import annotations

import json
from pathlib import Path
import aiosqlite


class Database:
    """Gerenciador assíncrono do banco de dados SQLite usando aiosqlite."""

    def __init__(self, db_path: str | Path = "bot.db") -> None:
        self.db_path = Path(db_path)
        self.conn: aiosqlite.Connection | None = None

    @staticmethod
    def parse_staff_roles(raw: str | None) -> list[int]:
        """Converte a coluna JSON staff_role_ids em uma lista de IDs inteiros."""
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if not isinstance(data, list):
            return []
        result: list[int] = []
        for item in data:
            try:
                role_id = int(item)
            except (TypeError, ValueError):
                continue
            if role_id not in result:
                result.append(role_id)
        return result

    async def init_db(self) -> None:
        """Abre a conexão com o SQLite e cria as tabelas necessárias."""
        self.conn = await aiosqlite.connect(self.db_path)
        self.conn.row_factory = aiosqlite.Row

        await self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS warns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER,
                user_id INTEGER,
                moderator_id INTEGER,
                reason TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS guild_config (
                guild_id INTEGER PRIMARY KEY,
                canal_logs INTEGER,
                categoria_tickets INTEGER,
                staff_role_ids TEXT DEFAULT '[]',
                canal_entrada INTEGER,
                canal_saida INTEGER,
                canal_punicoes INTEGER,
                canal_armadilha INTEGER,
                cargo_recrutados INTEGER,
                cargo_aprendiz INTEGER,
                cargo_samurai INTEGER
            );
            """
        )

        # Migração segura de esquemas antigos (cargo_staff único ou colunas em inglês)
        async with self.conn.execute("PRAGMA table_info(guild_config)") as cursor:
            existing_columns = {row["name"] for row in await cursor.fetchall()}

        legacy_columns = {
            "log_channel_id",
            "ticket_category_id",
            "staff_role_id",
            "cargo_staff",
            "welcome_channel_id",
            "leave_channel_id",
            "punishments_channel_id",
        }

        if existing_columns & legacy_columns:
            channel_map = {
                "canal_logs": "log_channel_id",
                "categoria_tickets": "ticket_category_id",
                "canal_entrada": "welcome_channel_id",
                "canal_saida": "leave_channel_id",
                "canal_punicoes": "punishments_channel_id",
                "canal_armadilha": None,
            }

            select_exprs = ["guild_id"]
            for new_col, old_col in channel_map.items():
                if new_col in existing_columns and old_col and old_col in existing_columns:
                    select_exprs.append(f"COALESCE({new_col}, {old_col}) AS {new_col}")
                elif new_col in existing_columns:
                    select_exprs.append(new_col)
                elif old_col and old_col in existing_columns:
                    select_exprs.append(f"{old_col} AS {new_col}")
                else:
                    select_exprs.append(f"NULL AS {new_col}")

            # Converte cargo_staff / staff_role_id único para lista JSON em staff_role_ids
            if "staff_role_ids" in existing_columns:
                staff_expr = "COALESCE(staff_role_ids, '[]')"
            elif "cargo_staff" in existing_columns and "staff_role_id" in existing_columns:
                staff_expr = (
                    "CASE WHEN COALESCE(cargo_staff, staff_role_id) IS NOT NULL "
                    "THEN '[' || COALESCE(cargo_staff, staff_role_id) || ']' ELSE '[]' END"
                )
            elif "cargo_staff" in existing_columns:
                staff_expr = (
                    "CASE WHEN cargo_staff IS NOT NULL "
                    "THEN '[' || cargo_staff || ']' ELSE '[]' END"
                )
            elif "staff_role_id" in existing_columns:
                staff_expr = (
                    "CASE WHEN staff_role_id IS NOT NULL "
                    "THEN '[' || staff_role_id || ']' ELSE '[]' END"
                )
            else:
                staff_expr = "'[]'"

            select_exprs.append(f"{staff_expr} AS staff_role_ids")

            await self.conn.executescript(
                f"""
                CREATE TABLE guild_config_new (
                    guild_id INTEGER PRIMARY KEY,
                    canal_logs INTEGER,
                    categoria_tickets INTEGER,
                    staff_role_ids TEXT DEFAULT '[]',
                    canal_entrada INTEGER,
                    canal_saida INTEGER,
                    canal_punicoes INTEGER,
                    canal_armadilha INTEGER,
                    cargo_recrutados INTEGER,
                    cargo_aprendiz INTEGER,
                    cargo_samurai INTEGER
                );
                INSERT INTO guild_config_new (
                    guild_id,
                    canal_logs,
                    categoria_tickets,
                    canal_entrada,
                    canal_saida,
                    canal_punicoes,
                    canal_armadilha,
                    staff_role_ids
                )
                SELECT {", ".join(select_exprs)} FROM guild_config;
                DROP TABLE guild_config;
                ALTER TABLE guild_config_new RENAME TO guild_config;
                """
            )
        else:
            expected_columns = {
                "canal_logs": "INTEGER",
                "categoria_tickets": "INTEGER",
                "staff_role_ids": "TEXT DEFAULT '[]'",
                "canal_entrada": "INTEGER",
                "canal_saida": "INTEGER",
                "canal_punicoes": "INTEGER",
                "canal_armadilha": "INTEGER",
                "cargo_recrutados": "INTEGER",
                "cargo_aprendiz": "INTEGER",
                "cargo_samurai": "INTEGER",
            }
            for col, col_type in expected_columns.items():
                if col not in existing_columns:
                    await self.conn.execute(
                        f"ALTER TABLE guild_config ADD COLUMN {col} {col_type}"
                    )

            if "canal_analise_divisao" in existing_columns:
                await self.conn.execute(
                    "ALTER TABLE guild_config DROP COLUMN canal_analise_divisao"
                )

        await self.conn.commit()

    async def get_guild_config(self, guild_id: int) -> aiosqlite.Row | None:
        """Retorna a configuração salva do servidor, se existir."""
        assert self.conn is not None
        async with self.conn.execute(
            """
            SELECT
                guild_id,
                canal_logs,
                categoria_tickets,
                staff_role_ids,
                canal_entrada,
                canal_saida,
                canal_punicoes,
                canal_armadilha,
                cargo_recrutados,
                cargo_aprendiz,
                cargo_samurai
            FROM guild_config
            WHERE guild_id = ?
            """,
            (guild_id,),
        ) as cursor:
            return await cursor.fetchone()

    async def upsert_guild_config(
        self,
        guild_id: int,
        *,
        canal_logs: int | None = None,
        categoria_tickets: int | None = None,
        staff_role_ids: list[int] | None = None,
        canal_entrada: int | None = None,
        canal_saida: int | None = None,
        canal_punicoes: int | None = None,
        canal_armadilha: int | None = None,
    ) -> aiosqlite.Row:
        """Salva ou atualiza parcialmente a configuração do servidor."""
        assert self.conn is not None

        serialized_staff: str | None = None
        if staff_role_ids is not None:
            unique_ids = list(dict.fromkeys(int(r) for r in staff_role_ids))
            serialized_staff = json.dumps(unique_ids)

        await self.conn.execute(
            """
            INSERT INTO guild_config (
                guild_id,
                canal_logs,
                categoria_tickets,
                staff_role_ids,
                canal_entrada,
                canal_saida,
                canal_punicoes,
                canal_armadilha
            )
            VALUES (?, ?, ?, COALESCE(?, '[]'), ?, ?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                canal_logs = COALESCE(excluded.canal_logs, guild_config.canal_logs),
                categoria_tickets = COALESCE(excluded.categoria_tickets, guild_config.categoria_tickets),
                staff_role_ids = CASE
                    WHEN ? IS NOT NULL THEN ?
                    ELSE COALESCE(guild_config.staff_role_ids, '[]')
                END,
                canal_entrada = COALESCE(excluded.canal_entrada, guild_config.canal_entrada),
                canal_saida = COALESCE(excluded.canal_saida, guild_config.canal_saida),
                canal_punicoes = COALESCE(excluded.canal_punicoes, guild_config.canal_punicoes),
                canal_armadilha = COALESCE(excluded.canal_armadilha, guild_config.canal_armadilha)
            """,
            (
                guild_id,
                canal_logs,
                categoria_tickets,
                serialized_staff,
                canal_entrada,
                canal_saida,
                canal_punicoes,
                canal_armadilha,
                serialized_staff,
                serialized_staff,
            ),
        )
        await self.conn.commit()
        row = await self.get_guild_config(guild_id)
        assert row is not None
        return row

    async def clear_trap_channel(self, guild_id: int) -> None:
        """Remove o canal armadilha configurado para o servidor."""
        assert self.conn is not None
        await self.conn.execute(
            """
            UPDATE guild_config
            SET canal_armadilha = NULL
            WHERE guild_id = ?
            """,
            (guild_id,),
        )
        await self.conn.commit()

    async def set_division_roles(
        self,
        guild_id: int,
        *,
        cargo_recrutados: int,
        cargo_aprendiz: int,
        cargo_samurai: int | None = None,
    ) -> aiosqlite.Row:
        """Salva os cargos usados no painel de testes de divisão."""
        assert self.conn is not None
        await self.conn.execute(
            """
            INSERT INTO guild_config (
                guild_id,
                cargo_recrutados,
                cargo_aprendiz,
                cargo_samurai
            )
            VALUES (?, ?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                cargo_recrutados = excluded.cargo_recrutados,
                cargo_aprendiz = excluded.cargo_aprendiz,
                cargo_samurai = excluded.cargo_samurai
            """,
            (guild_id, cargo_recrutados, cargo_aprendiz, cargo_samurai),
        )
        await self.conn.commit()
        row = await self.get_guild_config(guild_id)
        assert row is not None
        return row

    async def get_staff_roles(self, guild_id: int) -> list[int]:
        """Retorna a lista de IDs de cargos de staff configurados para o servidor."""
        config = await self.get_guild_config(guild_id)
        if not config:
            return []
        return self.parse_staff_roles(config["staff_role_ids"])

    async def add_staff_role(self, guild_id: int, role_id: int) -> list[int]:
        """Adiciona um cargo à lista de staff do servidor e retorna a lista atualizada."""
        roles = await self.get_staff_roles(guild_id)
        if role_id not in roles:
            roles.append(role_id)
        await self.upsert_guild_config(guild_id, staff_role_ids=roles)
        return roles

    async def remove_staff_role(self, guild_id: int, role_id: int) -> tuple[bool, list[int]]:
        """Remove um cargo da lista de staff e retorna (removido, lista_atualizada)."""
        roles = await self.get_staff_roles(guild_id)
        if role_id not in roles:
            return False, roles
        roles.remove(role_id)
        await self.upsert_guild_config(guild_id, staff_role_ids=roles)
        return True, roles

    async def add_warn(
        self,
        guild_id: int,
        user_id: int,
        moderator_id: int,
        reason: str,
    ) -> tuple[int, int]:
        """Registra uma advertência e retorna (warn_id, total_warns)."""
        assert self.conn is not None
        cursor = await self.conn.execute(
            """
            INSERT INTO warns (guild_id, user_id, moderator_id, reason)
            VALUES (?, ?, ?, ?)
            """,
            (guild_id, user_id, moderator_id, reason),
        )
        warn_id = cursor.lastrowid
        await self.conn.commit()

        async with self.conn.execute(
            "SELECT COUNT(*) FROM warns WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        ) as count_cursor:
            row = await count_cursor.fetchone()
            total = int(row[0]) if row else 1

        assert warn_id is not None
        return int(warn_id), total

    async def get_warns(self, guild_id: int, user_id: int) -> list[aiosqlite.Row]:
        """Retorna todas as advertências de um usuário no servidor."""
        assert self.conn is not None
        async with self.conn.execute(
            """
            SELECT id, guild_id, user_id, moderator_id, reason, created_at
            FROM warns
            WHERE guild_id = ? AND user_id = ?
            ORDER BY id DESC
            """,
            (guild_id, user_id),
        ) as cursor:
            rows = await cursor.fetchall()
            return list(rows)

    async def delete_warn(self, guild_id: int, warn_id: int) -> aiosqlite.Row | None:
        """Remove uma advertência específica pelo ID e retorna o registro removido."""
        assert self.conn is not None
        async with self.conn.execute(
            """
            SELECT id, guild_id, user_id, moderator_id, reason, created_at
            FROM warns
            WHERE guild_id = ? AND id = ?
            """,
            (guild_id, warn_id),
        ) as cursor:
            row = await cursor.fetchone()

        if row is None:
            return None

        await self.conn.execute(
            "DELETE FROM warns WHERE guild_id = ? AND id = ?",
            (guild_id, warn_id),
        )
        await self.conn.commit()
        return row

    async def close(self) -> None:
        """Fecha a conexão com o banco de dados."""
        if self.conn is not None:
            await self.conn.close()
            self.conn = None
