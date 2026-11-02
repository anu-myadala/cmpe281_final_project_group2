"""AppContainer: the long-lived objects of one API process (engine, Mongo
database, token service, event bus, WebSocket hub, clients, caches). It is
built once in create_app and reached through request.app.state.container, so
a test can swap a client for a fake."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Union

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from .clients import AlertClient, LocalStorageClient, MailSender, MqttPublisher, StorageClient, VehicleClient, make_storage
from .config import Settings
from .core.cache import RateCounter, TtlCache
from .core.eventbus import EventBus
from .core.security import PasswordHasher, TokenService
from .db import make_engine, make_mongo, make_session_factory
from .repos import EventQueryRepo
from .ws.hub import WsHub


@dataclass
class AppContainer:
    settings: Settings
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    mongo: Any
    tokens: TokenService
    hasher: PasswordHasher
    bus: EventBus
    hub: WsHub
    vehicle_client: VehicleClient
    alert_client: AlertClient
    storage: Union[StorageClient, LocalStorageClient]
    mail: MailSender
    mqtt: MqttPublisher
    overview_cache: TtlCache
    map_cache: TtlCache
    pins_cache: TtlCache
    login_rate: RateCounter
    events: EventQueryRepo = field(init=False)
    # Latest status report of each classifier worker (POST /internal/worker/status), by workerId.
    workers: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.events = EventQueryRepo(self.mongo)

    @classmethod
    def build(cls, settings: Settings) -> "AppContainer":
        engine = make_engine(settings.database_url)
        tokens = TokenService(settings)
        bus = EventBus()
        return cls(
            settings=settings,
            engine=engine,
            session_factory=make_session_factory(engine),
            mongo=make_mongo(settings.mongo_url, settings.mongo_database_name),
            tokens=tokens,
            hasher=PasswordHasher(),
            bus=bus,
            hub=WsHub(bus, tokens),
            vehicle_client=VehicleClient(settings, tokens),
            alert_client=AlertClient(settings, tokens),
            storage=make_storage(settings),
            mail=MailSender(settings),
            mqtt=MqttPublisher(settings),
            overview_cache=TtlCache(settings.overview_cache_seconds),
            map_cache=TtlCache(settings.map_cache_seconds),
            pins_cache=TtlCache(settings.map_cache_seconds),
            login_rate=RateCounter(settings.login_rate_limit_per_minute),
        )
