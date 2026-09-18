"""The sixteen German Länder: the single closed list shared by content, profiles and the UI.

Values are the official names, exactly as the learner profile form has always stored them.
"""

from enum import StrEnum


class Land(StrEnum):
    BADEN_WUERTTEMBERG = "Baden-Württemberg"
    BAYERN = "Bayern"
    BERLIN = "Berlin"
    BRANDENBURG = "Brandenburg"
    BREMEN = "Bremen"
    HAMBURG = "Hamburg"
    HESSEN = "Hessen"
    MECKLENBURG_VORPOMMERN = "Mecklenburg-Vorpommern"
    NIEDERSACHSEN = "Niedersachsen"
    NORDRHEIN_WESTFALEN = "Nordrhein-Westfalen"
    RHEINLAND_PFALZ = "Rheinland-Pfalz"
    SAARLAND = "Saarland"
    SACHSEN = "Sachsen"
    SACHSEN_ANHALT = "Sachsen-Anhalt"
    SCHLESWIG_HOLSTEIN = "Schleswig-Holstein"
    THUERINGEN = "Thüringen"


# Two-letter codes (ISO 3166-2:DE), used to build stable case identifiers.
LAND_CODES: dict[Land, str] = {
    Land.BADEN_WUERTTEMBERG: "BW",
    Land.BAYERN: "BY",
    Land.BERLIN: "BE",
    Land.BRANDENBURG: "BB",
    Land.BREMEN: "HB",
    Land.HAMBURG: "HH",
    Land.HESSEN: "HE",
    Land.MECKLENBURG_VORPOMMERN: "MV",
    Land.NIEDERSACHSEN: "NI",
    Land.NORDRHEIN_WESTFALEN: "NW",
    Land.RHEINLAND_PFALZ: "RP",
    Land.SAARLAND: "SL",
    Land.SACHSEN: "SN",
    Land.SACHSEN_ANHALT: "ST",
    Land.SCHLESWIG_HOLSTEIN: "SH",
    Land.THUERINGEN: "TH",
}
