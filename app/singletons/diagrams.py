import pandas as pd
import plotext as plt

from app.utils.singletons import store
from app.utils.helpers import singleton


@singleton
class Diagrams:

    def print_diagrams(self):
        print("Drucke Diagramme...")

        # Daten aus CSV laden
        df = pd.read_csv(store.filename_historic_data)
        df["Zeitpunkt"] = pd.to_datetime(
            df["Zeitpunkt"], format="mixed", errors="coerce"
        )
        df.dropna(subset=["Zeitpunkt"], inplace=True)

        # Zeitachse vorbereiten (Strings für Konsole)
        zeiten = df["Zeitpunkt"].dt.strftime("%d/%m/%Y %H:%M:%S").tolist()
        werte = df["Wert"].tolist()

        # Optionale Reduzierung der Werteanzahl für bessere Übersicht
        step = max(1, len(zeiten) // 100)
        zeiten = zeiten[::step]
        werte = werte[::step]

        # Diagramm erzeugen
        plt.clear_figure()
        plt.date_form("d/m/Y H:M:S")  # Passendes Datumsformat einstellen!
        plt.title("Kursverlauf (Konsolenansicht)")
        plt.xlabel("Zeit")
        plt.ylabel("Wert")
        plt.plot(zeiten, werte, marker="dot", color="cyan")

        plt.theme("pro")  # schönere Farben für die Konsole
        plt.canvas_color("default")
        plt.axes_color("default")

        # Diagramm in der Konsole ausgeben
        plt.show()
