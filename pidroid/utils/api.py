from __future__ import annotations

import datetime

from discord import Message, Member
from typing import TYPE_CHECKING, List, Optional

from pidroid.cogs.commands.tags import Tag
from pidroid.models.guild_configuration import GuildConfiguration
from pidroid.models.plugins import NewPlugin, Plugin
from pidroid.models.punishments import Case, PunishmentType
from pidroid.models.accounts import TheoTownAccount
from pidroid.models.role_changes import MemberRoleChanges, RoleAction, RoleQueueState
from pidroid.utils.http import HTTP, Route
from pidroid.utils.levels import MemberLevelInfo, LevelReward
from pidroid.utils.time import utcnow

from sqlalchemy import Column # type: ignore
from sqlalchemy import DateTime
from sqlalchemy import func, delete, select, update
from sqlalchemy import Integer, BigInteger, Text, ARRAY, Boolean, Float
from sqlalchemy import String
from sqlalchemy.dialects.postgresql import insert as pg_insert # type: ignore
from sqlalchemy.ext.asyncio import AsyncSession, AsyncEngine # type: ignore
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import declarative_base # type: ignore
from sqlalchemy.orm import sessionmaker

if TYPE_CHECKING:
    from pidroid.client import Pidroid

Base = declarative_base()

class LinkedAccountTable(Base): # type: ignore
    __tablename__ = "LinkedAccounts"

    id = Column(BigInteger, primary_key=True)
    user_id = Column(BigInteger)
    forum_id = Column(BigInteger)
    date_wage_last_redeemed = Column(DateTime(timezone=True), nullable=True)

class PunishmentCounterTable(Base): # type: ignore
    __tablename__ = "PunishmentCounters"

    id = Column(BigInteger, primary_key=True)
    guild_id = Column(BigInteger, unique=True)
    counter = Column(BigInteger, server_default="1")

class PunishmentTable(Base): # type: ignore
    __tablename__ = "Punishments"

    id = Column(BigInteger, primary_key=True) # global ID
    case_id = Column(BigInteger) # guild specific ID

    type = Column(Text)
    guild_id = Column(BigInteger)
    
    user_id = Column(BigInteger)
    user_name = Column(Text, nullable=True)

    moderator_id = Column(BigInteger)
    moderator_name = Column(Text, nullable=True)

    reason = Column(Text, nullable=True)

    issue_date = Column(DateTime(timezone=True), default=func.now()) # date of issue
    expire_date = Column(DateTime(timezone=True), nullable=True) # date when punishment expires, null means never

    # This let's us now if Pidroid already dealt with this case automatically
    handled = Column(Boolean, server_default="false")
    # This hides the case from visibility
    # Usually in the case of removing invalid warnings
    visible = Column(Boolean, server_default="true")

class ExpiringThreadTable(Base): # type: ignore
    __tablename__ = "ExpiringThreads"

    id = Column(BigInteger, primary_key=True)
    thread_id = Column(BigInteger)
    expiration_date = Column(DateTime(timezone=True))

class SuggestionTable(Base): # type: ignore
    __tablename__ = "Suggestions"

    id = Column(BigInteger, primary_key=True)
    author_id = Column(BigInteger)
    message_id = Column(BigInteger)
    suggestion = Column(Text)
    date_submitted = Column(DateTime(timezone=True), default=func.now())
    attachments = Column(ARRAY(Text), server_default="{}") # type: ignore

class TagTable(Base): # type: ignore
    __tablename__ = "Tags"

    id = Column(BigInteger, primary_key=True)
    guild_id = Column(BigInteger)
    name = Column(Text)
    content = Column(Text)
    authors = Column(ARRAY(BigInteger)) # type: ignore
    aliases = Column(ARRAY(Text), server_default="{}") # type: ignore
    locked = Column(Boolean, server_default="false")
    date_created = Column(DateTime(timezone=True), default=func.now())

class GuildConfigurationTable(Base): # type: ignore
    __tablename__ = "GuildConfigurations"

    id = Column(BigInteger, primary_key=True)
    guild_id = Column(BigInteger)
    jail_channel = Column(BigInteger, nullable=True)
    jail_role = Column(BigInteger, nullable=True)
    log_channel = Column(BigInteger, nullable=True)
    prefixes = Column(ARRAY(Text), server_default="{}") # type: ignore
    suspicious_usernames = Column(ARRAY(Text), server_default="{}") # type: ignore
    public_tags = Column(Boolean, server_default="false")
    punishing_moderators = Column(Boolean, server_default="false")
    appeal_url = Column(String, nullable=True)
    
    # Leveling system related
    xp_system_active = Column(Boolean, server_default="false")
    xp_per_message_min = Column(BigInteger, server_default="15")
    xp_per_message_max = Column(BigInteger, server_default="25")
    xp_multiplier = Column(Float, server_default="1.0")
    xp_exempt_roles = Column(ARRAY(BigInteger), server_default="{}") # type: ignore
    xp_exempt_channels = Column(ARRAY(BigInteger), server_default="{}") # type: ignore
    stack_level_rewards = Column(Boolean, server_default="true")

    # Suggestion system related
    suggestion_system_active = Column(Boolean, server_default="false")
    suggestion_channel = Column(BigInteger, nullable=True)
    suggestion_threads_enabled = Column(Boolean, server_default="false")

class UserLevelsTable(Base): # type: ignore
    __tablename__ = "UserLevels"
    
    id = Column(BigInteger, primary_key=True)
    guild_id = Column(BigInteger)
    user_id = Column(BigInteger)
    total_xp = Column(BigInteger, server_default="0")
    current_xp = Column(BigInteger, server_default="0")
    xp_to_next_level = Column(BigInteger, server_default="100")
    level = Column(BigInteger, server_default="0")
    progress_character = Column(String, nullable=True)

class LevelRewardsTable(Base): # type: ignore
    __tablename__ = "LevelRewards"
    
    id = Column(BigInteger, primary_key=True)
    guild_id = Column(BigInteger)
    level = Column(BigInteger)
    role_id = Column(BigInteger)

class TranslationTable(Base): # type: ignore
    __tablename__ = "Translations"

    id = Column(Integer, primary_key=True)
    original_content = Column(String)
    detected_language = Column(String)
    translated_string = Column(String)

class RoleChangeQueueTable(Base): # type: ignore
    __tablename__ = "RoleChangeQueue"
    
    id = Column(BigInteger, primary_key=True)
    action = Column(Integer) # RoleAction
    status = Column(Integer) # RoleQueueState
    guild_id = Column(BigInteger)
    member_id = Column(BigInteger)
    role_id = Column(BigInteger)
    date_created = Column(DateTime(timezone=True), default=func.now())

class API:
    """This class handles operations related to Pidroid's Postgres database and remote TheoTown API."""

    def __init__(self, client: Pidroid, dsn: str) -> None:
        self.client = client
        self.__dsn = dsn
        self.__http = HTTP(client)
        self.__engine: Optional[AsyncEngine] = None

    async def connect(self) -> None:
        """Creates a postgresql database connection."""
        self.__engine = create_async_engine(self.__dsn, echo=self.client.debugging)
        self.session = sessionmaker(self.__engine, expire_on_commit=False, class_=AsyncSession) # type: ignore
        # Checks if actual connection can be made
        temp_conn = await self.__engine.connect()
        await temp_conn.close()

    async def get(self, route: Route) -> dict:
        """Sends a GET request to the TheoTown API."""
        return await self.__http.request("GET", route)

    async def insert_suggestion(self, author_id: int, message_id: int, suggestion: str, attachment_urls: List[str] = []) -> int:
        """Creates a suggestion entry in the database."""
        async with self.session() as session: # type: ignore
            assert isinstance(session, AsyncSession)
            async with session.begin():
                entry = SuggestionTable(
                    author_id=author_id,
                    message_id=message_id,
                    suggestion=suggestion,
                    attachments=attachment_urls
                )
                session.add(entry)
            await session.commit()
        return entry.id # type: ignore

    """Tag related"""

    async def insert_tag(self, guild_id: int, name: str, content: str, authors: List[int]) -> int:
        """Creates a tag entry in the database."""
        async with self.session() as session: # type: ignore
            assert isinstance(session, AsyncSession)
            async with session.begin():
                entry = TagTable(
                    guild_id=guild_id,
                    name=name,
                    content=content,
                    authors=authors
                )
                session.add(entry)
            await session.commit()
        return entry.id # type: ignore

    async def fetch_guild_tag(self, guild_id: int, tag_name: str) -> Optional[Tag]:
        """Returns a guild tag for the appropriate name."""
        async with self.session() as session: # type: ignore
            assert isinstance(session, AsyncSession)
            result = await session.execute(
                select(TagTable).
                filter(TagTable.guild_id == guild_id, TagTable.name.ilike(tag_name)).
                order_by(TagTable.name.asc())
            )
        row = result.fetchone()
        if row:
            return Tag(self.client, row[0])
        return None

    async def search_guild_tags(self, guild_id: int, tag_name: str) -> List[Tag]:
        """Returns all guild tags matching the appropriate name."""
        async with self.session() as session: # type: ignore
            assert isinstance(session, AsyncSession)
            result = await session.execute(
                select(TagTable).
                filter(TagTable.guild_id == guild_id, TagTable.name.ilike(f'%{tag_name}%')).
                order_by(TagTable.name.asc())
            )
        return [Tag(self.client, r[0]) for r in result.fetchall()]

    async def fetch_guild_tags(self, guild_id: int) -> List[Tag]:
        """Returns a list of all tags defined in the guild."""
        async with self.session() as session: # type: ignore
            assert isinstance(session, AsyncSession)
            result = await session.execute(
                select(TagTable).
                filter(TagTable.guild_id == guild_id).
                order_by(TagTable.name.asc())
            )
        return [Tag(self.client, r[0]) for r in result.fetchall()]

    async def update_tag(self, row_id: int, content: str, authors: List[int], aliases: List[str], locked: bool) -> None:
        """Updates a tag entry by specified row ID."""
        async with self.session() as session: # type: ignore
            assert isinstance(session, AsyncSession)
            async with session.begin():
                await session.execute(
                    update(TagTable).
                    filter(TagTable.id == row_id).
                    values(
                        content=content,
                        authors=authors,
                        aliases=aliases,
                        locked=locked
                    )
                )
            await session.commit()

    async def delete_tag(self, row_id: int) -> None:
        """Removes a tag by specified row ID."""
        async with self.session() as session: # type: ignore
            assert isinstance(session, AsyncSession)
            async with session.begin():
                await session.execute(delete(TagTable).filter(TagTable.id == row_id))
            await session.commit()

    """Guild configuration related"""

    async def insert_guild_configuration(
        self,
        guild_id: int,
        jail_channel: Optional[int] = None, jail_role: Optional[int] = None,
        log_channel: Optional[int] = None
    ) -> GuildConfiguration:
        """Inserts a minimal guild configuration entry to the database."""
        async with self.session() as session: # type: ignore
            assert isinstance(session, AsyncSession)
            async with session.begin():
                entry = GuildConfigurationTable(
                    guild_id=guild_id,
                    jail_channel=jail_channel,
                    jail_role=jail_role,
                    log_channel=log_channel
                )
                session.add(entry)
            await session.commit()
        config = await self.__fetch_guild_configuration_by_id(entry.id) # type: ignore
        assert config is not None
        return config

    async def __fetch_guild_configuration_by_id(self, id: int) -> Optional[GuildConfiguration]:
        """Fetches and returns a deserialized guild configuration if available."""
        async with self.session() as session: # type: ignore
            assert isinstance(session, AsyncSession)
            result = await session.execute(
                select(GuildConfigurationTable).
                filter(GuildConfigurationTable.id == id)
            )
        r = result.fetchone()
        if r:
            return GuildConfiguration.from_table(self, r[0])
        return None

    async def fetch_guild_configuration(self, guild_id: int) -> Optional[GuildConfiguration]:
        """Fetches and returns a deserialized guild configuration if available for the specified guild."""
        async with self.session() as session: # type: ignore
            assert isinstance(session, AsyncSession)
            result = await session.execute(
                select(GuildConfigurationTable).
                filter(GuildConfigurationTable.guild_id == guild_id)
            )
        r = result.fetchone()
        if r:
            return GuildConfiguration.from_table(self, r[0])
        return None

    async def fetch_guild_configurations(self) -> List[GuildConfiguration]:
        """Returns a list of all guild configuration entries in the database."""
        async with self.session() as session: # type: ignore
            assert isinstance(session, AsyncSession)
            result = await session.execute(
                select(GuildConfigurationTable)
            )
        return [GuildConfiguration.from_table(self, r[0]) for r in result.fetchall()]

    async def _update_guild_configuration(
        self,
        row_id: int,
        *,
        jail_channel: Optional[int],
        jail_role: Optional[int],
        log_channel: Optional[int],
        prefixes: List[str],
        public_tags: bool,
        punishing_moderators: bool,
        appeal_url: Optional[str],

        xp_system_active: bool,
        xp_multiplier: float,
        xp_exempt_roles: List[int],
        xp_exempt_channels: List[int],
        stack_level_rewards: bool,

        suggestion_system_active: bool,
        suggestion_channel: Optional[int],
        suggestion_threads_enabled: bool
    ) -> None:
        """Updates a guild configuration entry by specified row ID."""
        async with self.session() as session: # type: ignore
            assert isinstance(session, AsyncSession)
            async with session.begin():
                await session.execute(
                    update(GuildConfigurationTable).
                    filter(GuildConfigurationTable.id == row_id).
                    values(
                        jail_channel=jail_channel,
                        jail_role=jail_role,
                        log_channel=log_channel,
                        prefixes=prefixes,
                        public_tags=public_tags,
                        punishing_moderators=punishing_moderators,
                        appeal_url=appeal_url,

                        xp_system_active=xp_system_active,
                        xp_multiplier=xp_multiplier,
                        xp_exempt_roles=xp_exempt_roles,
                        xp_exempt_channels=xp_exempt_channels,
                        stack_level_rewards=stack_level_rewards,

                        suggestion_system_active=suggestion_system_active,
                        suggestion_channel=suggestion_channel,
                        suggestion_threads_enabled=suggestion_threads_enabled
                    )
                )
            await session.commit()

    async def delete_guild_configuration(self, row_id: int) -> None:
        """Removes a guild configuration entry by specified row ID."""
        async with self.session() as session: # type: ignore
            assert isinstance(session, AsyncSession)
            async with session.begin():
                await session.execute(delete(GuildConfigurationTable).filter(GuildConfigurationTable.id == row_id))
            await session.commit()

    """Expiring thread related"""

    async def insert_expiring_thread(self, thread_id: int, expiration_date: datetime.datetime) -> None:
        async with self.session() as session: # type: ignore
            assert isinstance(session, AsyncSession)
            async with session.begin():
                session.add(ExpiringThreadTable(thread_id=thread_id, expiration_date=expiration_date))
            await session.commit()

    async def fetch_expired_threads(self, expiration_date: datetime.datetime) -> List[ExpiringThreadTable]:
        """Returns a list of active threads which require archiving."""
        async with self.session() as session: # type: ignore
            assert isinstance(session, AsyncSession)
            result = await session.execute(
                select(ExpiringThreadTable).
                filter(ExpiringThreadTable.expiration_date <= expiration_date)
            )
        return [r[0] for r in result.fetchall()] # type: ignore

    async def delete_expiring_thread(self, row_id: int) -> None:
        """Removes an expiring thread entry from the database."""
        async with self.session() as session: # type: ignore
            assert isinstance(session, AsyncSession)
            async with session.begin():
                await session.execute(delete(ExpiringThreadTable).filter(ExpiringThreadTable.id == row_id))
            await session.commit()

    """Punishment related"""

    async def insert_punishment_entry(
        self,
        type: str,
        guild_id: int,
        user_id: int, user_name: str,
        moderator_id: int, moderator_name: str,
        reason: Optional[str],
        expire_date: Optional[datetime.datetime]
    ) -> Case:
        async with self.session() as session: # type: ignore
            assert isinstance(session, AsyncSession)
            async with session.begin():
                insert_stmt = pg_insert(PunishmentCounterTable).values(guild_id=guild_id).on_conflict_do_update(
                    index_elements=[PunishmentCounterTable.guild_id],
                    set_=dict(counter=PunishmentCounterTable.counter + 1)
                ).returning(PunishmentCounterTable.counter)

                res = await session.execute(insert_stmt)
                row = res.fetchone()
                if row is None:
                    raise RuntimeError((
                        f"After trying to insert a punishment entry, row was None\n\n"
                        "Information about punishment:\n"
                        f"type: {type}\n"
                        f"guild_id: {guild_id}\n"
                        f"user_id: {user_id}\n"
                        f"moderator_id: {moderator_id}\n"
                        f"reason: {reason}\n"
                        f"expire_date: {expire_date}"
                    ))
                counter = row[0]

                entry = PunishmentTable(
                    case_id=counter,
                    type=type,
                    guild_id=guild_id,
                    user_id=user_id,
                    user_name=user_name,
                    moderator_id=moderator_id,
                    moderator_name=moderator_name,
                    reason=reason,
                    expire_date=expire_date
                )
                session.add(entry)
            await session.commit()

        case = await self.__fetch_case_by_internal_id(entry.id) # type: ignore
        assert case is not None
        return case

    async def __fetch_case_by_internal_id(self, id: int) -> Optional[Case]:
        """Fetches and returns a deserialized case, if available."""
        async with self.session() as session: # type: ignore
            assert isinstance(session, AsyncSession)
            result = await session.execute(
                select(PunishmentTable).
                filter(PunishmentTable.id == id)
            )
        r = result.fetchone()
        if r:
            c = Case(self)
            await c._from_table(r[0])
            return c
        return None

    async def _fetch_case(self, guild_id: int, case_id: int) -> Optional[Case]:
        """Fetches and returns a deserialized case if available."""
        async with self.session() as session: # type: ignore
            assert isinstance(session, AsyncSession)
            result = await session.execute(
                select(PunishmentTable).
                filter(
                    PunishmentTable.case_id == case_id,
                    PunishmentTable.guild_id == guild_id,
                    PunishmentTable.visible == True
                )
            )
        r = result.fetchone()
        if r:
            c = Case(self)
            await c._from_table(r[0])
            return c
        return None

    async def _fetch_cases(self, guild_id: int, user_id: int) -> List[Case]:
        """Fetches and returns a list of deserialized cases."""
        async with self.session() as session: # type: ignore
            assert isinstance(session, AsyncSession)
            result = await session.execute(
                select(PunishmentTable).
                filter(
                    PunishmentTable.guild_id == guild_id,
                    PunishmentTable.user_id == user_id,
                    PunishmentTable.visible == True
                ).
                order_by(PunishmentTable.issue_date.desc())
            )
        case_list = []
        for r in result.fetchall():
            c = Case(self)
            await c._from_table(r[0])
            case_list.append(c)
        return case_list
    
    async def _fetch_cases_by_username(self, guild_id: int, username: str) -> List[Case]:
        """Returns all cases in the guild where original username at punishment time matches the provided username."""
        async with self.session() as session: # type: ignore
            assert isinstance(session, AsyncSession)
            result = await session.execute(
                select(PunishmentTable).
                filter(
                    PunishmentTable.guild_id == guild_id,
                    PunishmentTable.user_name.ilike(f'%{username}%'),
                    PunishmentTable.visible == True
                ).
                order_by(PunishmentTable.user_name.asc())
            )
        case_list = []
        for r in result.fetchall():
            c = Case(self)
            await c._from_table(r[0])
            case_list.append(c)
        return case_list

    async def update_case_by_internal_id(
        self,
        id: int,
        reason: Optional[str],
        expire_date: Optional[datetime.datetime],
        visible: bool,
        handled: bool
    ) -> None:
        """Updates a case entry by specified row ID."""
        async with self.session() as session: # type: ignore
            assert isinstance(session, AsyncSession)
            async with session.begin():
                await session.execute(
                    update(PunishmentTable).
                    filter(PunishmentTable.id == id).
                    values(
                        reason=reason,
                        expire_date=expire_date,
                        visible=visible,
                        handled=handled
                    )
                )
            await session.commit()

    async def expire_cases_by_type(
        self,
        type: PunishmentType,
        guild_id: int, user_id: int
    ) -> None:
        """Expires a case entry by making it expired using the specified type, user ID and guild ID."""
        async with self.session() as session: # type: ignore
            assert isinstance(session, AsyncSession)
            async with session.begin():
                await session.execute(
                    update(PunishmentTable).
                    filter(PunishmentTable.type == type.value, PunishmentTable.guild_id == guild_id, PunishmentTable.user_id == user_id).
                    values(
                        # Note that we do not hide it, only set it as handled so Pidroid doesn't touch it
                        expire_date=utcnow(),
                        handled=True
                    )
                )
            await session.commit()

    async def fetch_moderation_statistics(self, guild_id: int, moderator_id: int) -> dict:
        """Fetches and returns a dictionary containing the general moderation statistics."""
        bans = kicks = jails = warnings = user_total = guild_total = 0
        async with self.session() as session: # type: ignore
            assert isinstance(session, AsyncSession)
            result = await session.execute(
                select(PunishmentTable.type).
                filter(
                    PunishmentTable.guild_id == guild_id,
                    PunishmentTable.moderator_id == moderator_id,
                    PunishmentTable.visible == True
                )
            )

            # Tally moderator specific data
            for r in result.fetchall():
                user_total += 1
                p_val = r[0]
                if p_val == 'ban': bans += 1         # noqa: E701
                if p_val == 'kick': kicks += 1       # noqa: E701
                if p_val == 'jail': jails += 1       # noqa: E701
                if p_val == 'warning': warnings += 1 # noqa: E701

            # Get total cases of the guild
            result = await session.execute(
                select(func.count(PunishmentTable.type)).
                filter(
                    PunishmentTable.guild_id == guild_id,
                    PunishmentTable.visible == True
                )
            )
            guild_total = result.fetchone()[0] # type: ignore

        return {
            "bans": bans, "kicks": kicks, "jails": jails, "warnings": warnings,
            "user_total": user_total,
            "guild_total": guild_total
        }

    async def __is_currently_punished(self, punishment_type: str, guild_id: int, user_id: int) -> bool:
        """Returns true if user is currently punished in a guild for the specified punishment type."""
        async with self.session() as session: # type: ignore
            assert isinstance(session, AsyncSession)
            result = await session.execute(
                select(func.count(PunishmentTable.type)).
                filter(
                    PunishmentTable.type == punishment_type,
                    PunishmentTable.guild_id == guild_id,
                    PunishmentTable.user_id == user_id,
                    PunishmentTable.visible == True
                ).
                filter(
                    (PunishmentTable.expire_date.is_(None))
                    | (PunishmentTable.expire_date > utcnow())
                )
            )
            return result.fetchone()[0] > 0 # type: ignore

    async def is_currently_jailed(self, guild_id: int, user_id: int) -> bool:
        """Returns true if user is currently jailed in the guild."""
        return await self.__is_currently_punished(PunishmentType.jail.value, guild_id, user_id)

    async def fetch_active_guild_bans(self, guild_id: int) -> List[Case]:
            """
            Returns a list of active guild bans that can expire. Bans
            without an expiration date will not be included.
            """
            async with self.session() as session: # type: ignore
                assert isinstance(session, AsyncSession)
                result = await session.execute(
                    select(PunishmentTable).
                    filter(
                        PunishmentTable.guild_id == guild_id,

                        # Expires if expiration date is past current date
                        # and if expiration date is actually set
                        PunishmentTable.expire_date <= utcnow(),
                        PunishmentTable.expire_date.is_not(None),

                        # Explicit statement to know if case was already handled
                        PunishmentTable.handled == False,

                        PunishmentTable.type == PunishmentType.ban.value,
                        PunishmentTable.visible == True
                    )
                )
            case_list = []
            for r in result.fetchall():
                c = Case(self)
                await c._from_table(r[0])
                case_list.append(c)
            return case_list

    """Translation related"""

    async def insert_translation_entry(self, original_str: str, detected_lang: str, translated_str: str) -> None:
        """Inserts a new translation entry to the database."""
        async with self.session() as session: # type: ignore
            assert isinstance(session, AsyncSession)
            async with session.begin():
                entry = TranslationTable(
                    original_content=original_str,
                    detected_language=detected_lang,
                    translated_string=translated_str
                )
                session.add(entry)
            await session.commit()

    async def fetch_translations(self, original_str: str) -> List[dict]:
        """Returns a list of translations for specified string."""
        async with self.session() as session: # type: ignore
            assert isinstance(session, AsyncSession)
            result = await session.execute(
                select(TranslationTable).
                filter(TranslationTable.original_content == original_str)
            )
        return [
            {"detected_source_language": r[0].detected_language, "text": r[0].translated_string} for r in result.fetchall()
        ]

    """Linked account related"""

    async def insert_linked_account(self, user_id: int, forum_id: int) -> int:
        """Creates a linked account entry in the database."""
        async with self.session() as session: # type: ignore
            assert isinstance(session, AsyncSession)
            async with session.begin():
                entry = LinkedAccountTable(
                    user_id=user_id,
                    forum_id=forum_id
                )
                session.add(entry)
            await session.commit()
        return entry.id  # type: ignore

    async def update_linked_account_by_user_id(
        self,
        user_id: int,
        date_wage_last_redeemed: Optional[datetime.datetime],
    ) -> None:
        """Updates a linked account entry by specified user ID."""
        async with self.session() as session: # type: ignore
            assert isinstance(session, AsyncSession)
            async with session.begin():
                await session.execute(
                    update(LinkedAccountTable).
                    filter(LinkedAccountTable.user_id == user_id).
                    values(
                       date_wage_last_redeemed=date_wage_last_redeemed
                    )
                )
            await session.commit()

    async def fetch_linked_account_by_user_id(self, user_id: int) -> Optional[LinkedAccountTable]:
        """Fetches and returns a linked account if available."""
        async with self.session() as session: # type: ignore
            assert isinstance(session, AsyncSession)
            result = await session.execute(
                select(LinkedAccountTable).
                filter(LinkedAccountTable.user_id == user_id)
            )
        r = result.fetchone()
        if r:
            return r[0]
        return None

    async def fetch_linked_account_by_forum_id(self, forum_id: int) -> Optional[LinkedAccountTable]:
        """Fetches and returns a linked account if available."""
        async with self.session() as session: # type: ignore
            assert isinstance(session, AsyncSession)
            result = await session.execute(
                select(LinkedAccountTable).
                filter(LinkedAccountTable.forum_id == forum_id)
            )
        r = result.fetchone()
        if r:
            return r[0]
        return None
    
    """Leveling system related"""

    async def insert_level_reward(self, guild_id: int, role_id: int, level: int) -> int:
        """Creates a level reward entry in the database."""
        async with self.session() as session: # type: ignore
            assert isinstance(session, AsyncSession)
            async with session.begin():
                entry = LevelRewardsTable(
                    guild_id=guild_id,
                    role_id=role_id,
                    level=level
                )
                session.add(entry)
            await session.commit()
        self.client.dispatch(
            'pidroid_level_reward_add',
            await self.fetch_level_reward_by_id(entry.id) # type: ignore
        )
        return entry.id # type: ignore

    async def update_level_reward_by_id(self, id: int, role_id: int, level: int) -> None:
        """Updates a level reward entry by specified ID."""
        async with self.session() as session: # type: ignore
            assert isinstance(session, AsyncSession)
            async with session.begin():
                await session.execute(
                    update(LevelRewardsTable).
                    filter(LevelRewardsTable.id == id).
                    values(
                       role_id=role_id,
                       level=level
                    )
                )
            await session.commit()

    async def _delete_level_reward(self, id: int) -> None:
        """Removes a level reward by specified row ID."""
        obj = await self.fetch_level_reward_by_id(id)
        async with self.session() as session: # type: ignore
            assert isinstance(session, AsyncSession)
            async with session.begin():
                await session.execute(delete(LevelRewardsTable).filter(LevelRewardsTable.id == id))
            await session.commit()
        self.client.dispatch("pidroid_level_reward_remove", obj)

    async def fetch_all_guild_level_rewards(self, guild_id: int) -> List[LevelReward]:
        """Returns a list of all LevelReward entries available for the specified guild.
        
        Role IDs are sorted by their appropriate level requirement descending."""
        async with self.session() as session: # type: ignore
            assert isinstance(session, AsyncSession)
            result = await session.execute(
                select(LevelRewardsTable).
                filter(
                    LevelRewardsTable.guild_id == guild_id
                ).
                order_by(LevelRewardsTable.level.desc())
            )
        data = []
        for r in result.fetchall():
            data.append(LevelReward.from_table(self, r[0]))
        return data

    async def fetch_level_reward_by_id(self, id: int) -> Optional[LevelReward]:
        """Returns a LevelReward entry for the specified ID."""
        async with self.session() as session: # type: ignore
            assert isinstance(session, AsyncSession)
            result = await session.execute(
                select(LevelRewardsTable).
                filter(LevelRewardsTable.id == id)
            )
        r = result.fetchone()
        if r:
            return LevelReward.from_table(self, r[0])
        return None

    async def fetch_level_reward_by_role(self, guild_id: int, role_id: int) -> Optional[LevelReward]:
        """Returns a LevelReward entry for the specified guild and role."""
        async with self.session() as session: # type: ignore
            assert isinstance(session, AsyncSession)
            result = await session.execute(
                select(LevelRewardsTable).
                filter(
                    LevelRewardsTable.guild_id == guild_id,
                    LevelRewardsTable.role_id == role_id
                )
            )
        r = result.fetchone()
        if r:
            return LevelReward.from_table(self, r[0])
        return None

    async def fetch_guild_level_reward_by_level(self, guild_id: int, level: int) -> Optional[LevelReward]:
        """Returns a LevelReward entry for the specified guild and level."""
        async with self.session() as session: # type: ignore
            assert isinstance(session, AsyncSession)
            result = await session.execute(
                select(LevelRewardsTable).
                filter(
                    LevelRewardsTable.guild_id == guild_id,
                    LevelRewardsTable.level == level
                )
            )
        r = result.fetchone()
        if r:
            return LevelReward.from_table(self, r[0])
        return None

    async def fetch_eligible_level_rewards_for_level(self, guild_id: int, level: int) -> List[LevelReward]:
        """Returns a list of LevelReward entries available for the specified guild and level.
        
        Entries are sorted by required level descending."""
        async with self.session() as session: # type: ignore
            assert isinstance(session, AsyncSession)
            result = await session.execute(
                select(LevelRewardsTable).
                filter(
                    LevelRewardsTable.guild_id == guild_id,
                    LevelRewardsTable.level <= level
                ).
                order_by(LevelRewardsTable.level.desc())
            )
        data = []
        for r in result.fetchall():
            data.append(LevelReward.from_table(self, r[0]))
        return data

    async def fetch_eligible_level_reward_for_level(self, guild_id: int, level: int) -> Optional[LevelReward]:
        """Returns a LevelReward entry for the specified guild and level.
        
        Entry will be sorted by required level descending."""
        rewards = await self.fetch_eligible_level_rewards_for_level(guild_id, level)
        if len(rewards) == 0:
            return None
        return rewards[0]

    async def _fetch_previous_level_reward(self, guild_id: int, level: int):
        async with self.session() as session: # type: ignore
            assert isinstance(session, AsyncSession)
            result = await session.execute(
                select(LevelRewardsTable).
                filter(
                    LevelRewardsTable.guild_id == guild_id,
                    LevelRewardsTable.level < level
                ).order_by(LevelRewardsTable.level.desc())
            )
        r = result.fetchone()
        if r:
            return LevelReward.from_table(self, r[0])
        return None

    async def _fetch_next_level_reward(self, guild_id: int, level: int):
        async with self.session() as session: # type: ignore
            assert isinstance(session, AsyncSession)
            result = await session.execute(
                select(LevelRewardsTable).
                filter(
                    LevelRewardsTable.guild_id == guild_id,
                    LevelRewardsTable.level > level
                ).order_by(LevelRewardsTable.level.desc())
            )
        r = result.fetchone()
        if r:
            return LevelReward.from_table(self, r[0])
        return None



    """ User levels related """

    async def insert_member_level_info(
        self,
        guild_id: int,
        user_id: int
    ) -> MemberLevelInfo:
        """Inserts an empty member level information entry to the database."""
        async with self.session() as session: # type: ignore
            assert isinstance(session, AsyncSession)
            async with session.begin():
                entry = UserLevelsTable(
                    guild_id=guild_id,
                    user_id=user_id
                )
                session.add(entry)
            await session.commit()
        info = await self.fetch_user_level_info(guild_id, user_id)
        assert info is not None
        return info

    async def fetch_guild_level_rankings(self, guild_id: int, start: int = 0, limit: int = 10) -> List[MemberLevelInfo]:
        """Returns a list of guild levels."""
        async with self.session() as session: # type: ignore
            assert isinstance(session, AsyncSession)
            result = await session.execute(
                select(
                    UserLevelsTable,
                    func.rank().over(order_by=UserLevelsTable.total_xp.desc()).label("rank")
                ).
                filter(UserLevelsTable.guild_id == guild_id).
                order_by(UserLevelsTable.total_xp.desc()).
                limit(limit).
                offset(start)
            )
        info = []
        for r in result.fetchall():
            info.append(MemberLevelInfo.from_table(self, r[0], r[1]))
        return info

    async def fetch_guild_level_infos(self, guild_id: int) -> List[MemberLevelInfo]:
        """Returns the bare member level information excluding ranking information."""
        async with self.session() as session: # type: ignore
            assert isinstance(session, AsyncSession)
            result = await session.execute(
                select(
                    UserLevelsTable
                ).
                filter(
                    UserLevelsTable.guild_id == guild_id
                )
            )
        data = []
        for r in result.fetchall():
            data.append(MemberLevelInfo.from_table(self, r[0]))
        return data

    async def fetch_ranked_user_level_info(self, guild_id: int, user_id: int) -> Optional[MemberLevelInfo]:
        """Returns ranked level information for the specified user."""
        async with self.session() as session: # type: ignore
            assert isinstance(session, AsyncSession)
            subquery = (
                select(
                    UserLevelsTable,
                    func.rank().over(order_by=UserLevelsTable.total_xp.desc()).label("rank")
                )
                .where(UserLevelsTable.guild_id == guild_id)
                .subquery()
            )
            query = (
                select(subquery)
                .where(subquery.c.user_id == user_id)
            )
            result = await session.execute(query)

            r = result.fetchone()

        if r:
            # I hate this, I couldn't figure out how to provide ORM entity + rank instead
            # If you have ideas, let me know
            # I spent 5 hours on this method
            row, guild_id, user_id, total_xp, current_xp, xp_to_level_up, level, char, rank = r
            return MemberLevelInfo(
                self,
                row,
                guild_id, user_id, total_xp, current_xp, xp_to_level_up, level,
                progress_character=char,
                rank=rank
            )
        return None

    async def fetch_user_level_info(self, guild_id: int, user_id: int) -> Optional[MemberLevelInfo]:
        """Returns the level information for the specified user."""
        async with self.session() as session: # type: ignore
            assert isinstance(session, AsyncSession)
            result = await session.execute(
                select(
                    UserLevelsTable
                ).
                filter(
                    UserLevelsTable.guild_id == guild_id,
                    UserLevelsTable.user_id == user_id
                )
            )
        r = result.fetchone()
        if r:
            return MemberLevelInfo.from_table(self, r[0])
        return None

    async def fetch_user_level_info_between(self, guild_id: int, min_level: int, max_level: Optional[int]) -> List[MemberLevelInfo]:
        """Returns a list of user level information for specified levels.
        
        Returned user data is ``min_level <= USERS < max_level`` """
        async with self.session() as session: # type: ignore
            assert isinstance(session, AsyncSession)
            stmt = (
                select(UserLevelsTable).
                filter(
                    UserLevelsTable.guild_id == guild_id,
                    UserLevelsTable.level >= min_level
                )
            )
            if max_level is not None:
                stmt = stmt.filter(UserLevelsTable.level < max_level)
            result = await session.execute(stmt)
        data = []
        for r in result.fetchall():
            data.append(MemberLevelInfo.from_table(self, r[0]))
        return data
    
    async def update_user_level_character(self, row_id: int, character: str):
        """Updates the user level character."""
        async with self.session() as session: # type: ignore
            assert isinstance(session, AsyncSession)
            async with session.begin():
                await session.execute(
                    update(UserLevelsTable).
                    filter(
                        UserLevelsTable.id == row_id
                    ).values(
                        progress_character=character
                    )
                )
            await session.commit()

    async def award_xp(self, message: Message, amount: int):
        """Awards the specified amount of XP to the specified message."""

        # We only award XP to messages in guilds from members
        assert message.guild is not None
        assert isinstance(message.author, Member)
        guild_id = message.guild.id
        member_id = message.author.id
        
        # Acquire the level information before leveling up
        info = await self.fetch_user_level_info(guild_id, member_id)
        new_insert = False
        if info is None:
            new_insert = True
            info = await self.insert_member_level_info(guild_id, member_id)

        # Get the current information
        original_xp = info.current_xp
        original_level = info.current_level
        original_xp_to_next_lvl = info.xp_to_level_up

        # If we get no level up
        new_xp_to_next_level = original_xp_to_next_lvl
        new_level = original_level
        new_xp = original_xp + amount

        # Calculate the new levels
        while True:
            if new_xp >= new_xp_to_next_level:
                new_level = new_level + 1
                # https://github.com/Mee6/Mee6-documentation/blob/master/docs/levels_xp.md
                new_xp = new_xp - new_xp_to_next_level
                new_xp_to_next_level = 5 * (new_level ** 2) + (50 * new_level) + 100
            else:
                break

        async with self.session() as session: # type: ignore
            assert isinstance(session, AsyncSession)
            async with session.begin():
                await session.execute(
                    update(UserLevelsTable).
                    filter(
                        UserLevelsTable.guild_id == guild_id,
                        UserLevelsTable.user_id == member_id
                    ).values(
                        current_xp=new_xp,
                        xp_to_next_level=new_xp_to_next_level,
                        total_xp=UserLevelsTable.total_xp + amount,
                        level=new_level
                    )
                )
            await session.commit()

        if new_insert or new_level != original_level:
            self.client.dispatch(
                'pidroid_level_up',
                message.author,
                message,
                info,
                MemberLevelInfo(
                    self,
                    info.row,
                    guild_id,
                    member_id,
                    info.total_xp + amount,
                    new_xp,
                    new_xp_to_next_level,
                    new_level,
                    progress_character=info.progress_character
                )
            )

    """Role change queue management in postgres database"""

    async def insert_role_change(self, action: RoleAction, guild_id: int, member_id: int, role_id: int):
        """Inserts a role change to a queue."""
        async with self.session() as session: # type: ignore
            assert isinstance(session, AsyncSession)
            async with session.begin():
                entry = RoleChangeQueueTable(
                    action=action.value,
                    status=RoleQueueState.enqueued.value,
                    guild_id=guild_id,
                    member_id=member_id,
                    role_id=role_id
                )
                session.add(entry)
            await session.commit()

    async def fetch_role_changes(self, guild_id: int) -> List[MemberRoleChanges]:
        """Returns a list of pending role changes in the guild."""
        async with self.session() as session: # type: ignore
            assert isinstance(session, AsyncSession)
            statement = select(
                func.array_agg(RoleChangeQueueTable.id).label('ids'),
                RoleChangeQueueTable.member_id,
                func.array_agg(RoleChangeQueueTable.role_id).filter(RoleChangeQueueTable.action == 1).label('role_added'),
                func.array_agg(RoleChangeQueueTable.role_id).filter(RoleChangeQueueTable.action == 0).label('role_removed')
            ).where(
                RoleChangeQueueTable.guild_id == guild_id
            ).group_by(
                RoleChangeQueueTable.member_id
            )
            result = await session.execute(statement)

        changes = []
        for row in result.fetchall():
            ids, member_id, roles_added, roles_removed = row
            obj = MemberRoleChanges(self, guild_id, member_id, ids, roles_added, roles_removed)
            changes.append(obj)
        return changes
    
    async def delete_role_changes(self, ids: List[int]):
        """Removes role changes for specified IDs from the queue."""
        async with self.session() as session: # type: ignore
            assert isinstance(session, AsyncSession)
            async with session.begin():
                await session.execute(delete(RoleChangeQueueTable).filter(RoleChangeQueueTable.id.in_(ids)))
            await session.commit()

    """TheoTown backend related"""

    async def fetch_theotown_account_by_id(self, account_id: int) -> Optional[TheoTownAccount]:
        """Queries the TheoTown API for new plugins after the specified approval time."""
        response = await self.get(Route("/private/game/fetch_user", {"forum_id": account_id}))
        if response["success"]:
            return TheoTownAccount(response["data"])
        return None

    async def fetch_new_plugins(self, last_approval_time: int) -> List[NewPlugin]:
        """Queries the TheoTown API for new plugins after the specified approval time."""
        response = await self.get(Route("/private/plugin/get_new", {"last_approval_time": last_approval_time}))
        return [NewPlugin(np) for np in response["data"]]

    async def fetch_plugin_by_id(self, plugin_id: int, show_hidden: bool = False) -> List[Plugin]:
        """Queries the TheoTown API for a plugin of the specified ID."""
        response = await self.get(Route(
            "/private/plugin/find2",
            {"id": plugin_id, "show_hidden": show_hidden}
        ))
        return [Plugin(p) for p in response["data"]]

    async def search_plugins(self, query: str, show_hidden: bool = False) -> List[Plugin]:
        """Queries the TheoTown API for plugins matching the query string."""
        response = await self.get(Route(
            "/private/plugin/find2",
            {"query": query, "show_hidden": show_hidden}
        ))
        return [Plugin(p) for p in response["data"]]
