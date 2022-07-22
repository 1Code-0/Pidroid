from __future__ import annotations

import datetime
import motor.motor_asyncio # type: ignore
import random

from bson.objectid import ObjectId # type: ignore
from discord.ext.commands.errors import BadArgument # type: ignore
from motor.core import AgnosticCollection # type: ignore
from typing import Any, List, Optional, Union, TYPE_CHECKING

from cogs.ext.commands.tags import Tag
from cogs.models.case import Case
from cogs.models.configuration import GuildConfiguration
from cogs.models.plugins import NewPlugin, Plugin
from cogs.utils.http import HTTP, Route
from cogs.utils.time import utcnow

from sqlalchemy import Column # type: ignore
from sqlalchemy import DateTime
from sqlalchemy import func, text, delete, select, update, values
from sqlalchemy import Integer, BigInteger, Text, ARRAY, Boolean
from sqlalchemy import String
from sqlalchemy.dialects.postgresql import insert as pg_insert # type: ignore
from sqlalchemy.engine.result import ChunkedIteratorResult # type: ignore
from sqlalchemy.ext.asyncio import AsyncSession, AsyncEngine # type: ignore
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import declarative_base # type: ignore
from sqlalchemy.orm import selectinload
from sqlalchemy.orm import sessionmaker

if TYPE_CHECKING:
    from client import Pidroid

Base = declarative_base()

class PunishmentCounterTable(Base): # type: ignore
    __tablename__ = "PunishmentCounters"

    id = Column(BigInteger, primary_key=True)
    guild_id = Column(BigInteger, unique=True)
    counter = Column(BigInteger, server_default="1")

class PunishmentTable(Base): # type: ignore
    __tablename__ = "Punishments"

    id = Column(BigInteger, primary_key=True)
    case_id = Column(BigInteger)

    type = Column(Text)
    guild_id = Column(BigInteger)
    
    user_id = Column(BigInteger)
    user_name = Column(Text, nullable=True)

    moderator_id = Column(BigInteger)
    moderator_name = Column(Text, nullable=True)

    reason = Column(Text, nullable=True)

    issue_date = Column(DateTime(timezone=True), default=func.now())
    expire_date = Column(DateTime(timezone=True), nullable=True)

    handled = Column(Boolean, server_default="false")
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
    attachments = Column(ARRAY(Text), server_default="{}")

class TagTable(Base): # type: ignore
    __tablename__ = "Tags"

    id = Column(BigInteger, primary_key=True)
    guild_id = Column(BigInteger)
    name = Column(Text)
    content = Column(Text)
    authors = Column(ARRAY(BigInteger))
    aliases = Column(ARRAY(Text), server_default="{}")
    locked = Column(Boolean, server_default="false")
    date_created = Column(DateTime(timezone=True), default=func.now())

class GuildConfigurationTable(Base): # type: ignore
    __tablename__ = "GuildConfigurations"

    id = Column(BigInteger, primary_key=True)
    guild_id = Column(BigInteger)
    jail_channel = Column(BigInteger, nullable=True)
    jail_role = Column(BigInteger, nullable=True)
    mute_role = Column(BigInteger, nullable=True) # Deprecated, only used for backwards compatibility
    log_channel = Column(BigInteger, nullable=True)
    prefixes = Column(ARRAY(Text), server_default="{}")
    suspicious_usernames = Column(ARRAY(Text), server_default="{}")
    public_tags = Column(Boolean, server_default="false")
    strict_anti_phishing = Column(Boolean, server_default="false")

class PhishingUrlTable(Base): # type: ignore
    __tablename__ = "PhishingUrls"

    id = Column(BigInteger, primary_key=True)
    url = Column(String)

    # required in order to access columns with server defaults
    # or SQL expression defaults, subsequent to a flush, without
    # triggering an expired load
    #__mapper_args__ = {"eager_defaults": True}

class API:
    """This class handles operations related to Pidroid's Postgres database and remote TheoTown API."""

    def __init__(self, client: Pidroid, dsn: str, debug: bool = False) -> None:
        self.client = client
        self._dsn = dsn
        self._debug = debug
        self._http = HTTP(client)
        self.engine: Optional[AsyncEngine] = None

    async def connect(self) -> None:
        self.engine = create_async_engine(self._dsn, echo=self._debug)
        self.session = sessionmaker(self.engine, expire_on_commit=False, class_=AsyncSession)

        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)

    async def get(self, route: Route) -> dict:
        """Sends a GET request to the TheoTown API."""
        return await self._http.request("GET", route)

    async def insert_phishing_url(self, url: str) -> None:
        """Inserts a phishing link to the database."""
        async with self.session() as session:
            assert isinstance(session, AsyncSession)
            async with session.begin():
                session.add(PhishingUrlTable(url=url))
            await session.commit()

    async def fetch_phishing_urls(self) -> List[str]:
        """Fetches a list of known phishing links and urls."""
        async with self.session() as session:
            assert isinstance(session, AsyncSession)
            result: ChunkedIteratorResult = await session.execute(select(PhishingUrlTable.url))
        return [r[0] for r in result.fetchall()]

    async def insert_suggestion(self, author_id: int, message_id: int, suggestion: str, attachment_urls: List[str] = []) -> int:
        """Creates a suggestion entry in the database."""
        async with self.session() as session:
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
        return entry.id

    """Tag related"""

    async def insert_tag(self, guild_id: int, name: str, content: str, authors: List[int]) -> int:
        """Creates a tag entry in the database."""
        async with self.session() as session:
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
        return entry.id

    async def fetch_guild_tag(self, guild_id: int, tag_name: str) -> Optional[Tag]:
        """Returns a guild tag for the appropriate name."""
        async with self.session() as session:
            assert isinstance(session, AsyncSession)
            result: ChunkedIteratorResult = await session.execute(
                select(TagTable).
                filter(TagTable.guild_id == guild_id, TagTable.name.ilike(tag_name)).
                order_by(TagTable.name.asc())
            )
        row = result.fetchone()
        if row:
            return Tag(self, row[0])
        return None

    async def search_guild_tags(self, guild_id: int, tag_name: str) -> List[Tag]:
        """Returns all guild tags matching the appropriate name."""
        async with self.session() as session:
            assert isinstance(session, AsyncSession)
            result: ChunkedIteratorResult = await session.execute(
                select(TagTable).
                filter(TagTable.guild_id == guild_id, TagTable.name.ilike(f'%{tag_name}%')).
                order_by(TagTable.name.asc())
            )
        return [Tag(self, r[0]) for r in result.fetchall()]

    async def fetch_guild_tags(self, guild_id: int) -> List[Tag]:
        """Returns a list of all tags defined in the guild."""
        async with self.session() as session:
            assert isinstance(session, AsyncSession)
            result: ChunkedIteratorResult = await session.execute(
                select(TagTable).
                filter(TagTable.guild_id == guild_id).
                order_by(TagTable.name.asc())
            )
        return [Tag(self, r[0]) for r in result.fetchall()]

    async def update_tag(self, row_id: int, content: str, authors: List[int], aliases: List[str], locked: bool) -> None:
        """Updates a tag entry by specified row ID."""
        async with self.session() as session:
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
        async with self.session() as session:
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
        async with self.session() as session:
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
        config = await self.fetch_guild_configuration_by_id(entry.id)
        assert config is not None
        return config

    async def fetch_guild_configuration_by_id(self, id: int) -> Optional[GuildConfiguration]:
        """Fetches and returns a deserialized guild configuration if available."""
        async with self.session() as session:
            assert isinstance(session, AsyncSession)
            result: ChunkedIteratorResult = await session.execute(
                select(GuildConfigurationTable).
                filter(GuildConfigurationTable.id == id)
            )
        r = result.fetchone()
        if r:
            return GuildConfiguration(self, r[0])
        return None

    async def fetch_guild_configurations(self) -> List[GuildConfiguration]:
        """Returns a list of all guild configuration entries in the database."""
        async with self.session() as session:
            assert isinstance(session, AsyncSession)
            result: ChunkedIteratorResult = await session.execute(
                select(GuildConfigurationTable)
            )
        return [GuildConfiguration(self, r[0]) for r in result.fetchall()]

    async def update_guild_configuration(
        self,
        row_id: int,
        jail_channel: Optional[int], jail_role: Optional[int],
        mute_role: Optional[int],
        log_channel: Optional[int],
        prefixes: List[str],
        suspicious_usernames: List[str],
        public_tags: bool, strict_anti_phishing: bool
    ) -> None:
        """Updates a guild configuration entry by specified row ID."""
        async with self.session() as session:
            assert isinstance(session, AsyncSession)
            async with session.begin():
                await session.execute(
                    update(GuildConfigurationTable).
                    filter(GuildConfigurationTable.id == row_id).
                    values(
                        jail_channel=jail_channel,
                        jail_role=jail_role,
                        mute_role=mute_role,
                        log_channel=log_channel,
                        prefixes=prefixes,
                        suspicious_usernames=suspicious_usernames,
                        public_tags=public_tags,
                        strict_anti_phishing=strict_anti_phishing
                    )
                )
            await session.commit()

    async def delete_guild_configuration(self, row_id: int) -> None:
        """Removes a guild configuration entry by specified row ID."""
        async with self.session() as session:
            assert isinstance(session, AsyncSession)
            async with session.begin():
                await session.execute(delete(GuildConfigurationTable).filter(GuildConfigurationTable.id == row_id))
            await session.commit()

    """Expiring thread related"""

    async def insert_expiring_thread(self, thread_id: int, expiration_date: datetime.datetime) -> None:
        async with self.session() as session:
            assert isinstance(session, AsyncSession)
            async with session.begin():
                session.add(ExpiringThreadTable(thread_id=thread_id, expiration_date=expiration_date))
            await session.commit()

    async def fetch_expired_threads(self, expiration_date: datetime.datetime) -> List[ExpiringThreadTable]:
        """Returns a list of active threads which require archiving."""
        async with self.session() as session:
            assert isinstance(session, AsyncSession)
            result: ChunkedIteratorResult = await session.execute(
                select(ExpiringThreadTable).
                filter(ExpiringThreadTable.expiration_date <= expiration_date)
            )
        return [r[0] for r in result.fetchall()]

    async def delete_expiring_thread(self, row_id: int) -> None:
        """Removes an expiring thread entry from the database."""
        async with self.session() as session:
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
        async with self.session() as session:
            assert isinstance(session, AsyncSession)
            async with session.begin():
                insert_stmt = pg_insert(PunishmentCounterTable).values(guild_id=guild_id).on_conflict_do_update(
                    index_elements=[PunishmentCounterTable.guild_id],
                    set_=dict(counter=PunishmentCounterTable.counter + 1) # TODO: investigate 
                ).returning(PunishmentCounterTable.counter)

                res = await session.execute(insert_stmt)
                counter = res.fetchone()[0]

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

        case = await self.fetch_case_by_internal_id(entry.id)
        assert case is not None
        return case

    async def fetch_case_by_internal_id(self, id: int) -> Optional[Case]:
        """Fetches and returns a deserialized case if available."""
        async with self.session() as session:
            assert isinstance(session, AsyncSession)
            result: ChunkedIteratorResult = await session.execute(
                select(PunishmentTable).
                filter(PunishmentTable.id == id)
            )
        r = result.fetchone()
        if r:
            c = Case(self, r[0])
            await c._fetch_users()
            return c
        return None

    async def fetch_case(self, guild_id: int, case_id: int) -> Optional[Case]:
        """Fetches and returns a deserialized case if available."""
        async with self.session() as session:
            assert isinstance(session, AsyncSession)
            result: ChunkedIteratorResult = await session.execute(
                select(PunishmentTable).
                filter(PunishmentTable.case_id == case_id, PunishmentTable.guild_id == guild_id, PunishmentTable.visible == True)
            )
        r = result.fetchone()
        if r:
            c = Case(self, r[0])
            await c._fetch_users()
            return c
        return None

    async def fetch_cases(self, guild_id: int, user_id: int) -> List[Case]:
        """Fetches and returns a list of deserialized cases."""
        async with self.session() as session:
            assert isinstance(session, AsyncSession)
            result: ChunkedIteratorResult = await session.execute(
                select(PunishmentTable).
                filter(PunishmentTable.guild_id == guild_id, PunishmentTable.user_id == user_id, PunishmentTable.visible == True).
                order_by(PunishmentTable.issue_date.desc())
            )
        case_list = []
        for r in result.fetchall():
            c = Case(self, r[0])
            await c._fetch_users()
            case_list.append(c)
        return case_list

    async def fetch_active_cases(self, guild_id: int, user_id: int) -> List[Case]:
        """Returns a list of active cases for the specified guild and user."""
        return [c for c in await self.fetch_cases(guild_id, user_id) if not c.has_expired]

    async def fetch_warnings(self, guild_id: int, user_id: int) -> List[Case]:
        """Returns a list of warnings for the specified guild and user."""
        return [c for c in await self.fetch_cases(guild_id, user_id) if c.type == "warning"]

    async def fetch_active_warnings(self, guild_id: int, user_id: int) -> List[Case]:
        """Returns a list of active warnings for the specified guild and user."""
        return [
            c
            for c in await self.fetch_cases(guild_id, user_id)
            if c.type == "warning" and not c.has_expired
        ]

    async def update_case_by_internal_id(
        self,
        id: int,
        reason: Optional[str],
        expire_date: Optional[datetime.datetime],
        visible: bool,
        handled: bool
    ) -> None:
        """Updates a case entry by specified row ID."""
        async with self.session() as session:
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

    async def revoke_cases_by_type(
        self,
        type: str,
        guild_id: int, user_id: int
    ) -> None:
        """Revokes a case entry by making it expired using the specified type, user ID and guild ID."""
        async with self.session() as session:
            assert isinstance(session, AsyncSession)
            async with session.begin():
                await session.execute(
                    update(PunishmentTable).
                    filter(PunishmentTable.type == type, PunishmentTable.guild_id == guild_id, PunishmentTable.user_id == user_id).
                    values(
                        expire_date=utcnow(),
                        handled=True
                    )
                )
            await session.commit()

    async def fetch_moderation_statistics(self, guild_id: int, moderator_id: int) -> dict:
        """Fetches and returns a dictionary containing the general moderation statistics."""
        bans = kicks = jails = warnings = guild_total = 0
        async with self.session() as session:
            assert isinstance(session, AsyncSession)
            result: ChunkedIteratorResult = await session.execute(
                select(PunishmentTable.type).
                filter(
                    PunishmentTable.guild_id == guild_id,
                    PunishmentTable.moderator_id == moderator_id,
                    PunishmentTable.visible == True
                )
            )

            # Tally moderator specific data
            for r in result.fetchall():
                p_val = r[0]
                if p_val == "ban": bans += 1         # noqa: E701
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
            guild_total = result.fetchone()[0]

        return {
            "bans": bans, "kicks": kicks, "jails": jails, "warnings": warnings,
            "user_total": bans + kicks + jails + warnings,
            "guild_total": guild_total
        }

    async def is_currently_punished(self, punishment_type: str, guild_id: int, user_id: int) -> bool:
        """Returns true if user is currently punished in a guild for the specified punishment type."""
        async with self.session() as session:
            assert isinstance(session, AsyncSession)
            result: ChunkedIteratorResult = await session.execute(
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
            return result.fetchone()[0] > 0

    async def is_currently_jailed(self, guild_id: int, user_id: int) -> bool:
        """Returns true if user is currently jailed in the guild."""
        return await self.is_currently_punished("jail", guild_id, user_id)

    async def is_currently_muted(self, guild_id: int, user_id: int) -> bool:
        """Returns true if user is currently muted in the guild."""
        return await self.is_currently_punished("mute", guild_id, user_id)

    async def fetch_active_expiring_guild_punishments(self, guild_id: int) -> List[Case]:
        """Returns a list of active guild punishments that can expire. Punishments
        without an expiration date will not be included.
        
        Additionally, warnings, timeouts, jails and kicks are excluded from this list
        as Pidroid doesn't have anything else to do."""
        async with self.session() as session:
            assert isinstance(session, AsyncSession)
            result: ChunkedIteratorResult = await session.execute(
                select(PunishmentTable).
                filter(
                    PunishmentTable.guild_id == guild_id,

                    # Expires if expiration date is past current date
                    # and if expiration date is actually set
                    PunishmentTable.expire_date <= utcnow(),
                    PunishmentTable.expire_date.is_not(None),

                    # Explicit statement to know if case was already handled
                    PunishmentTable.handled == False,

                    # Ignore things that cannot expire or Pidroid does not care
                    PunishmentTable.type != 'warning',
                    PunishmentTable.type != 'timeout',
                    PunishmentTable.type != 'jail',
                    PunishmentTable.type != 'kick',

                    PunishmentTable.visible == True
                )
            )
        case_list = []
        for r in result.fetchall():
            c = Case(self, r[0])
            await c._fetch_users()
            case_list.append(c)
        return case_list

    """TheoTown backend related"""

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

class DeprecatedAPI:
    """This class handles actions related to Pidroid Mongo and MySQL database calls over TheoTown API."""

    def __init__(self, client: Pidroid, connection_string: str):
        self.client = client
        self.db_client = motor.motor_asyncio.AsyncIOMotorClient(connection_string, serverSelectionTimeoutMS=5000)
        self.db = self.db_client["Pidroid"]
        self.http = HTTP(client)

    @property
    def internal(self) -> AgnosticCollection:
        """Returns a MongoDB collection for internal related data."""
        return self.db["Internal"]

    @property
    def shitposts(self) -> AgnosticCollection:
        """Returns a MongoDB collection for Pidroid shitposts."""
        return self.db["Shitposts"]

    @property
    def translations(self) -> AgnosticCollection:
        """Returns a MongoDB collection for translations."""
        return self.db["Translations"]

    """Phising protection related methods"""

    async def fetch_phising_url_list(self) -> List[str]:
        """Returns a list of phising URLs."""
        data = await self.internal.find_one({"phising.urls": {"$exists": True}}, {"phising": 1, "_id": 0})
        if data is None:
            self.client.logger.critical("Phising URLs returned an empty list!")
            return []
        return data["phising"]["urls"]

    """Translation related methods"""

    async def insert_new_translation(self, original: str, translation: List[dict]) -> None:
        """Inserts a new translation entry to the database."""
        await self.translations.insert_one({
            "original_string": original,
            "translation": translation
        })

    async def fetch_translation(self, original: str) -> List[dict]:
        """Returns a list of translations for specified string."""
        data = await self.translations.find_one({"original_string": original})
        if data is None:
            return []
        return data["translation"]

    """Shitpost related methods"""

    async def get_random_shitpost(self, last_posts: List[Union[ObjectId, int]]) -> dict:
        """Returns a random shitpost."""
        data = await self.shitposts.find_one(
            {"_id": {"$nin": last_posts}}
        ).sort("times_shown", 1)
        cursor = self.shitposts.find({
            "_id": {"$nin": last_posts}, "times_shown": data["times_shown"]
        })
        return random.choice([i async for i in cursor]) # nosec

    async def log_shitpost(self, object_id: ObjectId) -> None:
        """Marks shitpost by specified ID as read by increasing times_shown."""
        await self.shitposts.update_one(
            {'_id': object_id},
            {'$inc': {'times_shown': 1}}
        )
