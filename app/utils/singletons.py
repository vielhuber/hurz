_initialized = False

asset = None
autotrade = None
boot = None
database = None
diagrams = None
fulltest = None
history = None
livestats = None
menu = None
order = None
settings = None
store = None
training = None
utils = None
websocket = None


def bootstrap() -> None:
    global _initialized
    if _initialized:
        return

    global asset
    global autotrade
    global boot
    global database
    global diagrams
    global fulltest
    global history
    global livestats
    global menu
    global order
    global settings
    global store
    global training
    global utils
    global websocket

    import app.singletons.asset as import_asset
    import app.singletons.autotrade as import_autotrade
    import app.singletons.boot as import_boot
    import app.singletons.database as import_database
    import app.singletons.diagrams as import_diagrams
    import app.singletons.fulltest as import_fulltest
    import app.singletons.history as import_history
    import app.singletons.livestats as import_livestats
    import app.singletons.menu as import_menu
    import app.singletons.order as import_order
    import app.singletons.settings as import_settings
    import app.singletons.store as import_store
    import app.singletons.training as import_training
    import app.singletons.utils as import_utils
    import app.singletons.websocket as import_websocket

    from app.singletons.asset import Asset
    from app.singletons.autotrade import AutoTrade
    from app.singletons.boot import Boot
    from app.singletons.database import Database
    from app.singletons.diagrams import Diagrams
    from app.singletons.fulltest import FullTest
    from app.singletons.history import History
    from app.singletons.livestats import LiveStats
    from app.singletons.menu import Menu
    from app.singletons.order import Order
    from app.singletons.settings import Settings
    from app.singletons.store import Store
    from app.singletons.training import Training
    from app.singletons.utils import Utils
    from app.singletons.websocket import WebSocket

    asset = Asset()
    autotrade = AutoTrade()
    boot = Boot()
    database = Database()
    diagrams = Diagrams()
    fulltest = FullTest()
    history = History()
    livestats = LiveStats()
    menu = Menu()
    order = Order()
    settings = Settings()
    store = Store()
    training = Training()
    utils = Utils()
    websocket = WebSocket()

    import_asset.asset = asset
    import_asset.autotrade = autotrade
    import_asset.boot = boot
    import_asset.database = database
    import_asset.diagrams = diagrams
    import_asset.fulltest = fulltest
    import_asset.history = history
    import_asset.livestats = livestats
    import_asset.menu = menu
    import_asset.order = order
    import_asset.settings = settings
    import_asset.store = store
    import_asset.training = training
    import_asset.utils = utils
    import_asset.websocket = websocket

    import_autotrade.asset = asset
    import_autotrade.autotrade = autotrade
    import_autotrade.boot = boot
    import_autotrade.database = database
    import_autotrade.diagrams = diagrams
    import_autotrade.fulltest = fulltest
    import_autotrade.history = history
    import_autotrade.livestats = livestats
    import_autotrade.menu = menu
    import_autotrade.order = order
    import_autotrade.settings = settings
    import_autotrade.store = store
    import_autotrade.training = training
    import_autotrade.utils = utils
    import_autotrade.websocket = websocket

    import_boot.asset = asset
    import_boot.autotrade = autotrade
    import_boot.boot = boot
    import_boot.database = database
    import_boot.diagrams = diagrams
    import_boot.fulltest = fulltest
    import_boot.history = history
    import_boot.livestats = livestats
    import_boot.menu = menu
    import_boot.order = order
    import_boot.settings = settings
    import_boot.store = store
    import_boot.training = training
    import_boot.utils = utils
    import_boot.websocket = websocket

    import_database.asset = asset
    import_database.autotrade = autotrade
    import_database.boot = boot
    import_database.database = database
    import_database.diagrams = diagrams
    import_database.fulltest = fulltest
    import_database.history = history
    import_database.livestats = livestats
    import_database.menu = menu
    import_database.order = order
    import_database.settings = settings
    import_database.store = store
    import_database.training = training
    import_database.utils = utils
    import_database.websocket = websocket

    import_diagrams.asset = asset
    import_diagrams.autotrade = autotrade
    import_diagrams.boot = boot
    import_diagrams.database = database
    import_diagrams.diagrams = diagrams
    import_diagrams.fulltest = fulltest
    import_diagrams.history = history
    import_diagrams.livestats = livestats
    import_diagrams.menu = menu
    import_diagrams.order = order
    import_diagrams.settings = settings
    import_diagrams.store = store
    import_diagrams.training = training
    import_diagrams.utils = utils
    import_diagrams.websocket = websocket

    import_fulltest.asset = asset
    import_fulltest.autotrade = autotrade
    import_fulltest.boot = boot
    import_fulltest.database = database
    import_fulltest.diagrams = diagrams
    import_fulltest.fulltest = fulltest
    import_fulltest.history = history
    import_fulltest.livestats = livestats
    import_fulltest.menu = menu
    import_fulltest.order = order
    import_fulltest.settings = settings
    import_fulltest.store = store
    import_fulltest.training = training
    import_fulltest.utils = utils
    import_fulltest.websocket = websocket

    import_history.asset = asset
    import_history.autotrade = autotrade
    import_history.boot = boot
    import_history.database = database
    import_history.diagrams = diagrams
    import_history.fulltest = fulltest
    import_history.history = history
    import_history.livestats = livestats
    import_history.menu = menu
    import_history.order = order
    import_history.settings = settings
    import_history.store = store
    import_history.training = training
    import_history.utils = utils
    import_history.websocket = websocket

    import_livestats.asset = asset
    import_livestats.autotrade = autotrade
    import_livestats.boot = boot
    import_livestats.database = database
    import_livestats.diagrams = diagrams
    import_livestats.fulltest = fulltest
    import_livestats.history = history
    import_livestats.livestats = livestats
    import_livestats.menu = menu
    import_livestats.order = order
    import_livestats.settings = settings
    import_livestats.store = store
    import_livestats.training = training
    import_livestats.utils = utils
    import_livestats.websocket = websocket

    import_menu.asset = asset
    import_menu.autotrade = autotrade
    import_menu.boot = boot
    import_menu.database = database
    import_menu.diagrams = diagrams
    import_menu.fulltest = fulltest
    import_menu.history = history
    import_menu.livestats = livestats
    import_menu.menu = menu
    import_menu.order = order
    import_menu.settings = settings
    import_menu.store = store
    import_menu.training = training
    import_menu.utils = utils
    import_menu.websocket = websocket

    import_order.asset = asset
    import_order.autotrade = autotrade
    import_order.boot = boot
    import_order.database = database
    import_order.diagrams = diagrams
    import_order.fulltest = fulltest
    import_order.history = history
    import_order.livestats = livestats
    import_order.menu = menu
    import_order.order = order
    import_order.settings = settings
    import_order.store = store
    import_order.training = training
    import_order.utils = utils
    import_order.websocket = websocket

    import_settings.asset = asset
    import_settings.autotrade = autotrade
    import_settings.boot = boot
    import_settings.database = database
    import_settings.fulltest = fulltest
    import_settings.history = history
    import_settings.livestats = livestats
    import_settings.menu = menu
    import_settings.order = order
    import_settings.settings = settings
    import_settings.store = store
    import_settings.training = training
    import_settings.utils = utils
    import_settings.websocket = websocket

    import_store.asset = asset
    import_store.autotrade = autotrade
    import_store.boot = boot
    import_store.database = database
    import_store.diagrams = diagrams
    import_store.fulltest = fulltest
    import_store.history = history
    import_store.livestats = livestats
    import_store.menu = menu
    import_store.order = order
    import_store.settings = settings
    import_store.store = store
    import_store.training = training
    import_store.utils = utils
    import_store.websocket = websocket

    import_training.asset = asset
    import_training.autotrade = autotrade
    import_training.boot = boot
    import_training.database = database
    import_training.diagrams = diagrams
    import_training.fulltest = fulltest
    import_training.history = history
    import_training.livestats = livestats
    import_training.menu = menu
    import_training.order = order
    import_training.settings = settings
    import_training.store = store
    import_training.training = training
    import_training.utils = utils
    import_training.websocket = websocket

    import_utils.asset = asset
    import_utils.autotrade = autotrade
    import_utils.boot = boot
    import_utils.database = database
    import_utils.diagrams = diagrams
    import_utils.fulltest = fulltest
    import_utils.history = history
    import_utils.livestats = livestats
    import_utils.menu = menu
    import_utils.order = order
    import_utils.settings = settings
    import_utils.store = store
    import_utils.training = training
    import_utils.utils = utils
    import_utils.websocket = websocket

    import_websocket.asset = asset
    import_websocket.autotrade = autotrade
    import_websocket.boot = boot
    import_websocket.database = database
    import_websocket.diagrams = diagrams
    import_websocket.fulltest = fulltest
    import_websocket.history = history
    import_websocket.livestats = livestats
    import_websocket.menu = menu
    import_websocket.order = order
    import_websocket.settings = settings
    import_websocket.store = store
    import_websocket.training = training
    import_websocket.utils = utils
    import_websocket.websocket = websocket

    _initialized = True


bootstrap()
