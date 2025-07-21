import pandas as pd
import plotext as plt

from app.utils.singletons import store, utils, database
from app.utils.helpers import singleton


@singleton
class Diagrams:

    def print_diagrams(self) -> None:
        utils.print("ℹ️ Printing diagrams...", 1)

        # load data from database
        df = database.select('SELECT * FROM trading_data WHERE trade_asset = %s AND trade_platform = %s', (store.trade_asset, store.trade_platform))
        df = pd.DataFrame(df)
        df = df.rename(columns={'trade_asset': 'Waehrung', 'trade_platform': 'Plattform', 'timestamp': 'Zeitpunkt', 'price': 'Wert'})

        # format
        df['Wert'] = df['Wert'].astype(float)
        df["Zeitpunkt"] = pd.to_datetime(
            df["Zeitpunkt"], format="mixed", errors="coerce"
        )
        df.dropna(subset=["Zeitpunkt"], inplace=True)

        # prepare time axis (strings for console)
        zeiten = df["Zeitpunkt"].dt.strftime("%d/%m/%Y %H:%M:%S").tolist()
        werte = df["Wert"].tolist()

        # optional reduction of the number of values for better overview
        step = max(1, len(zeiten) // 100)
        zeiten = zeiten[::step]
        werte = werte[::step]

        # generate diagram
        plt.clear_figure()
        plt.date_form("d/m/Y H:M:S")  # set appropriate date format
        plt.title("Kursverlauf (Konsolenansicht)")
        plt.xlabel("Zeit")
        plt.ylabel("Wert")
        plt.plot(zeiten, werte, marker="dot", color="cyan")

        plt.theme("pro")  # prettier colors for console
        plt.canvas_color("default")
        plt.axes_color("default")

        # output diagram in console
        plt.show()
