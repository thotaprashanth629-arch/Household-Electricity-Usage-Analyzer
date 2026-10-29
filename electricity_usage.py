"""Household Electricity Usage Analyzer
Course End Project 10 (Batch-10), Section G -- Python Programming.

Reads household electricity meter records, calculates daily and monthly
usage, categorises consumption, stores processed records in a DBMS
(sqlite3), handles invalid readings, and generates monthly text reports.
Analysis covers high-consumption periods, household comparison, unusual
consumption and energy-saving suggestions.

Usage levels (kWh per month): Low <= 150, Medium 151-300, High > 300.
Sample from the project specification: Meter M208, previous reading
4820 kWh, current reading 5140 kWh, September -> 320 kWh, High.

Run:  python electricity_usage.py
Requires only the Python standard library.
"""

import os
import sqlite3
import sys
from datetime import datetime

DB_FILE = "electricity.db"
REPORT_DIR = "reports"

# Days in each month (non-leap year), used for average daily usage.
MONTH_DAYS = {1: 31, 2: 28, 3: 31, 4: 30, 5: 31, 6: 30,
              7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31}
MONTH_NAMES = {1: "January", 2: "February", 3: "March", 4: "April",
               5: "May", 6: "June", 7: "July", 8: "August",
               9: "September", 10: "October", 11: "November",
               12: "December"}


class ValidationError(Exception):
    """A reading or input value is invalid."""


class NotFoundError(Exception):
    """A household or record that was asked for does not exist."""


class DuplicateError(Exception):
    """Kept for API symmetry; readings use upsert instead of failing."""


def connect():
    """Return a connection to the DBMS with foreign keys switched on."""
    conn = sqlite3.connect(DB_FILE)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    """Create the households and readings tables if they do not exist."""
    with connect() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS households (
                   meter_id   TEXT PRIMARY KEY,
                   owner_name TEXT NOT NULL,
                   address    TEXT
               )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS readings (
                   id            INTEGER PRIMARY KEY AUTOINCREMENT,
                   meter_id      TEXT NOT NULL,
                   month_num     INTEGER NOT NULL CHECK (month_num BETWEEN 1 AND 12),
                   prev_reading  REAL NOT NULL CHECK (prev_reading >= 0),
                   curr_reading  REAL NOT NULL CHECK (curr_reading >= 0),
                   consumption   REAL NOT NULL,
                   usage_level   TEXT NOT NULL,
                   UNIQUE (meter_id, month_num),
                   FOREIGN KEY (meter_id) REFERENCES households (meter_id)
               )"""
        )


def usage_level(consumption):
    """Categorise a monthly consumption figure (kWh)."""
    if consumption <= 150:
        return "Low"
    if consumption <= 300:
        return "Medium"
    return "High"


def validate_reading(prev_reading, curr_reading):
    """Reject invalid readings; return the consumption in kWh."""
    if prev_reading < 0 or curr_reading < 0:
        raise ValidationError("Readings cannot be negative.")
    if curr_reading < prev_reading:
        raise ValidationError(
            "Current reading (%.1f) is below previous reading (%.1f); "
            "a meter reading cannot go backwards." % (curr_reading, prev_reading)
        )
    return curr_reading - prev_reading


def add_household(meter_id, owner_name, address=""):
    """Add a household; silently keep the existing one on duplicates."""
    with connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO households VALUES (?, ?, ?)",
            (meter_id.strip().upper(), owner_name.strip(), address.strip()),
        )


def get_household(meter_id):
    """Return a household row or raise NotFoundError."""
    with connect() as conn:
        row = conn.execute(
            "SELECT meter_id, owner_name, address FROM households WHERE meter_id = ?",
            (meter_id.strip().upper(),),
        ).fetchone()
    if row is None:
        raise NotFoundError("No household with meter id %s." % meter_id)
    return row


def list_households():
    """Return all households ordered by meter id."""
    with connect() as conn:
        return conn.execute(
            "SELECT meter_id, owner_name, address FROM households ORDER BY meter_id"
        ).fetchall()


def record_reading(meter_id, month_num, prev_reading, curr_reading):
    """Store (or update) one monthly reading; returns (consumption, level).

    A second entry for the same meter and month replaces the first one,
    so a corrected reading never creates a duplicate row.
    """
    meter_id = meter_id.strip().upper()
    get_household(meter_id)
    if month_num not in MONTH_DAYS:
        raise ValidationError("Month must be between 1 and 12.")
    consumption = validate_reading(prev_reading, curr_reading)
    level = usage_level(consumption)
    with connect() as conn:
        conn.execute(
            """INSERT INTO readings
                   (meter_id, month_num, prev_reading, curr_reading,
                    consumption, usage_level)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT (meter_id, month_num) DO UPDATE SET
                   prev_reading = excluded.prev_reading,
                   curr_reading = excluded.curr_reading,
                   consumption = excluded.consumption,
                   usage_level = excluded.usage_level""",
            (meter_id, month_num, prev_reading, curr_reading, consumption, level),
        )
    return consumption, level


def readings_for_month(month_num):
    """All readings for one month, with owner names, ordered by consumption."""
    with connect() as conn:
        return conn.execute(
            """SELECT r.meter_id, h.owner_name, r.consumption, r.usage_level,
                      r.prev_reading, r.curr_reading
               FROM readings r JOIN households h ON h.meter_id = r.meter_id
               WHERE r.month_num = ?
               ORDER BY r.consumption DESC""",
            (month_num,),
        ).fetchall()


def household_totals():
    """Total / average / peak consumption per household across all months."""
    with connect() as conn:
        return conn.execute(
            """SELECT r.meter_id, h.owner_name, COUNT(*),
                      SUM(r.consumption), AVG(r.consumption), MAX(r.consumption)
               FROM readings r JOIN households h ON h.meter_id = r.meter_id
               GROUP BY r.meter_id
               ORDER BY SUM(r.consumption) DESC"""
        ).fetchall()


def high_consumption(month_num=None):
    """Readings categorised High, optionally restricted to one month."""
    with connect() as conn:
        if month_num is None:
            return conn.execute(
                """SELECT r.meter_id, h.owner_name, r.month_num, r.consumption
                   FROM readings r JOIN households h ON h.meter_id = r.meter_id
                   WHERE r.usage_level = 'High'
                   ORDER BY r.consumption DESC"""
            ).fetchall()
        return conn.execute(
            """SELECT r.meter_id, h.owner_name, r.month_num, r.consumption
               FROM readings r JOIN households h ON h.meter_id = r.meter_id
               WHERE r.usage_level = 'High' AND r.month_num = ?
               ORDER BY r.consumption DESC""",
            (month_num,),
        ).fetchall()


def unusual_consumption():
    """Flag months whose consumption is over 1.5x the household's other months.

    A sudden jump (for example a faulty appliance or a meter fault) stands
    out against the household's own normal pattern.
    """
    flagged = []
    with connect() as conn:
        rows = conn.execute(
            "SELECT meter_id, month_num, consumption FROM readings ORDER BY meter_id, month_num"
        ).fetchall()
    by_meter = {}
    for meter_id, month_num, consumption in rows:
        by_meter.setdefault(meter_id, []).append((month_num, consumption))
    for meter_id, entries in by_meter.items():
        for month_num, consumption in entries:
            others = [c for m, c in entries if m != month_num]
            if not others:
                continue
            average = sum(others) / len(others)
            if average > 0 and consumption > 1.5 * average:
                flagged.append((meter_id, month_num, consumption, round(average, 1)))
    return sorted(flagged, key=lambda row: row[2], reverse=True)


def suggestions(level):
    """Energy-saving practices matched to a usage level."""
    if level == "High":
        return ("Replace high-wattage appliances, use LED lighting, run heavy "
                "appliances off-peak, and get the wiring and meter checked.")
    if level == "Medium":
        return ("Switch off standby devices, use fans instead of air "
                "conditioning where possible, and track usage weekly.")
    return ("Usage is efficient; keep the current habits and re-check "
            "after adding any new appliance.")


def monthly_report(month_num):
    """Write a timestamped text report for one month; return its path."""
    rows = readings_for_month(month_num)
    if not rows:
        raise NotFoundError("No readings recorded for %s." % MONTH_NAMES[month_num])
    total = sum(row[2] for row in rows)
    average = total / len(rows)
    highest = rows[0]
    lines = []
    lines.append("HOUSEHOLD ELECTRICITY USAGE REPORT")
    lines.append("Month: %s" % MONTH_NAMES[month_num])
    lines.append("Generated: %s" % datetime.now().strftime("%Y-%m-%d %H:%M"))
    lines.append("-" * 56)
    lines.append("%-10s %-16s %12s  %-8s %12s" % (
        "Meter", "Household", "Usage (kWh)", "Level", "Avg/day"))
    for meter_id, owner, consumption, level, _prev, _curr in rows:
        daily = consumption / MONTH_DAYS[month_num]
        lines.append("%-10s %-16s %12.1f  %-8s %12.2f" % (
            meter_id, owner, consumption, level, daily))
    lines.append("-" * 56)
    lines.append("Households reported : %d" % len(rows))
    lines.append("Total consumption  : %.1f kWh" % total)
    lines.append("Average consumption: %.1f kWh" % average)
    lines.append("Highest usage      : %s (%s, %.1f kWh)" % (
        highest[0], highest[1], highest[2]))
    lines.append("")
    lines.append("Energy-saving suggestion for the highest user:")
    lines.append(suggestions(highest[3]))
    os.makedirs(REPORT_DIR, exist_ok=True)
    path = os.path.join(
        REPORT_DIR,
        "usage_report_%02d_%s.txt" % (
            month_num, datetime.now().strftime("%Y%m%d_%H%M%S")),
    )
    with open(path, "w") as handle:
        handle.write("\n".join(lines) + "\n")
    return path, lines


SAMPLE_HOUSEHOLDS = [
    ("M201", "Ravi Kumar", "Street 1"),
    ("M202", "Sunitha", "Street 2"),
    ("M203", "Anil", "Street 3"),
    ("M204", "Priya", "Street 4"),
    ("M205", "Kiran", "Street 5"),
    ("M206", "Lakshmi", "Street 6"),
    ("M207", "Vikram", "Street 7"),
    ("M208", "Suresh", "Street 8"),
]

# (meter, month, previous reading, current reading)
SAMPLE_READINGS = [
    ("M201", 7, 1200, 1420), ("M201", 8, 1420, 1680), ("M201", 9, 1680, 1900),
    ("M202", 7, 800, 950), ("M202", 8, 950, 1130), ("M202", 9, 1130, 1290),
    ("M203", 7, 2000, 2350), ("M203", 8, 2350, 2720), ("M203", 9, 2720, 3050),
    ("M204", 7, 500, 620), ("M204", 8, 620, 760), ("M204", 9, 760, 880),
    ("M205", 7, 1500, 1750), ("M205", 8, 1750, 2050), ("M205", 9, 2050, 2600),
    ("M206", 7, 900, 1100), ("M206", 8, 1100, 1280), ("M206", 9, 1280, 1500),
    ("M207", 7, 3000, 3400), ("M207", 8, 3400, 3850), ("M207", 9, 3850, 4200),
    ("M208", 7, 4300, 4580), ("M208", 8, 4580, 4820), ("M208", 9, 4820, 5140),
]


def load_sample_data():
    """Load the built-in sample households and readings."""
    for meter_id, owner, address in SAMPLE_HOUSEHOLDS:
        add_household(meter_id, owner, address)
    for meter_id, month_num, prev_reading, curr_reading in SAMPLE_READINGS:
        record_reading(meter_id, month_num, prev_reading, curr_reading)
    return len(SAMPLE_HOUSEHOLDS), len(SAMPLE_READINGS)


def read_float(prompt):
    """Read a number from the user, retrying on non-numeric input."""
    while True:
        try:
            return float(input(prompt))
        except ValueError:
            print("Please enter a number.")


def read_int(prompt, low=None, high=None):
    """Read an integer from the user within an optional range."""
    while True:
        try:
            value = int(input(prompt))
        except ValueError:
            print("Please enter a whole number.")
            continue
        if low is not None and value < low:
            print("Value must be at least %d." % low)
            continue
        if high is not None and value > high:
            print("Value must be at most %d." % high)
            continue
        return value


def menu_manage_households():
    """Add a household and list all households."""
    print("\n--- Manage Households ---")
    meter_id = input("Meter ID (e.g. M208): ").strip()
    owner = input("Household owner name: ").strip()
    address = input("Address (optional): ").strip()
    if not meter_id or not owner:
        print("Meter ID and owner name are required.")
        return
    add_household(meter_id, owner, address)
    print("Household %s saved." % meter_id.upper())
    print("\n%-10s %-16s %s" % ("Meter", "Owner", "Address"))
    for meter_id, owner, address in list_households():
        print("%-10s %-16s %s" % (meter_id, owner, address or "-"))


def menu_record_reading():
    """Record one monthly reading, with invalid-reading handling."""
    print("\n--- Record Monthly Reading ---")
    meter_id = input("Meter ID: ").strip()
    try:
        get_household(meter_id)
    except NotFoundError as error:
        print("Error:", error)
        return
    month_num = read_int("Month (1-12): ", 1, 12)
    prev_reading = read_float("Previous reading (kWh): ")
    curr_reading = read_float("Current reading (kWh): ")
    try:
        consumption, level = record_reading(
            meter_id, month_num, prev_reading, curr_reading)
    except ValidationError as error:
        print("Invalid reading rejected:", error)
        return
    print("Consumption: %.1f kWh" % consumption)
    print("Usage Level: %s" % level)
    print("Average daily usage: %.2f kWh/day" % (
        consumption / MONTH_DAYS[month_num]))


def menu_monthly_usage():
    """Show one month's usage table with daily averages."""
    print("\n--- Monthly Usage ---")
    month_num = read_int("Month (1-12): ", 1, 12)
    rows = readings_for_month(month_num)
    if not rows:
        print("No readings for %s yet." % MONTH_NAMES[month_num])
        return
    print("\n%-10s %-16s %12s  %-8s %12s" % (
        "Meter", "Household", "Usage (kWh)", "Level", "Avg/day"))
    for meter_id, owner, consumption, level, _prev, _curr in rows:
        print("%-10s %-16s %12.1f  %-8s %12.2f" % (
            meter_id, owner, consumption, level,
            consumption / MONTH_DAYS[month_num]))
    total = sum(row[2] for row in rows)
    print("Total: %.1f kWh   Average: %.1f kWh" % (total, total / len(rows)))


def menu_high_consumption():
    """List every High-level reading, highest first."""
    print("\n--- High-Consumption Households ---")
    rows = high_consumption()
    if not rows:
        print("No High consumption recorded yet.")
        return
    print("%-10s %-16s %-12s %s" % ("Meter", "Household", "Month", "Usage (kWh)"))
    for meter_id, owner, month_num, consumption in rows:
        print("%-10s %-16s %-12s %.1f" % (
            meter_id, owner, MONTH_NAMES[month_num], consumption))


def menu_compare():
    """Compare households by total, average and peak usage."""
    print("\n--- Compare Households ---")
    rows = household_totals()
    if not rows:
        print("No readings recorded yet.")
        return
    print("%-10s %-16s %8s %12s %12s %12s" % (
        "Meter", "Household", "Months", "Total (kWh)", "Average", "Peak"))
    for meter_id, owner, count, total, average, peak in rows:
        print("%-10s %-16s %8d %12.1f %12.1f %12.1f" % (
            meter_id, owner, count, total, average, peak))
    print("Highest total usage: %s (%s, %.1f kWh)" % (
        rows[0][0], rows[0][1], rows[0][3]))


def menu_unusual():
    """Show months flagged as unusual against the household's own pattern."""
    print("\n--- Unusual Consumption ---")
    rows = unusual_consumption()
    if not rows:
        print("No unusual consumption detected.")
        return
    print("%-10s %-12s %12s %18s" % (
        "Meter", "Month", "Usage (kWh)", "Other-month average"))
    for meter_id, month_num, consumption, average in rows:
        print("%-10s %-12s %12.1f %18.1f" % (
            meter_id, MONTH_NAMES[month_num], consumption, average))
    print("Possible causes: faulty appliance, meter fault, extra occupants, "
          "or a new high-wattage device.")


def menu_suggestions():
    """Print energy-saving suggestions for a household's latest level."""
    print("\n--- Energy-Saving Suggestions ---")
    meter_id = input("Meter ID: ").strip()
    try:
        get_household(meter_id)
    except NotFoundError as error:
        print("Error:", error)
        return
    with connect() as conn:
        row = conn.execute(
            """SELECT month_num, consumption, usage_level FROM readings
               WHERE meter_id = ? ORDER BY month_num DESC LIMIT 1""",
            (meter_id.upper(),),
        ).fetchone()
    if row is None:
        print("No readings for %s yet." % meter_id.upper())
        return
    month_num, consumption, level = row
    print("%s, %s: %.1f kWh (%s)" % (
        meter_id.upper(), MONTH_NAMES[month_num], consumption, level))
    print(suggestions(level))


def menu_report():
    """Generate a monthly text report file."""
    print("\n--- Generate Monthly Text Report ---")
    month_num = read_int("Month (1-12): ", 1, 12)
    try:
        path, lines = monthly_report(month_num)
    except NotFoundError as error:
        print("Error:", error)
        return
    print("\n".join(lines))
    print("\nReport saved to %s" % path)


MENU = [
    ("1", "Manage Households", menu_manage_households),
    ("2", "Record Monthly Reading", menu_record_reading),
    ("3", "View Monthly Usage", menu_monthly_usage),
    ("4", "High-Consumption Households", menu_high_consumption),
    ("5", "Compare Households", menu_compare),
    ("6", "Unusual Consumption", menu_unusual),
    ("7", "Energy-Saving Suggestions", menu_suggestions),
    ("8", "Generate Monthly Text Report", menu_report),
]


def main():
    """Menu loop for the Household Electricity Usage Analyzer."""
    init_db()
    print("=" * 56)
    print("HOUSEHOLD ELECTRICITY USAGE ANALYZER")
    print("Course End Project 10 (Batch-10), Section G")
    print("=" * 56)
    while True:
        print("\n--- Menu ---")
        for key, label, _func in MENU:
            print("%s. %s" % (key, label))
        print("9. Load Sample Data")
        print("0. Exit")
        choice = input("Enter your choice: ").strip()
        if choice == "0":
            print("Goodbye.")
            return
        if choice == "9":
            households, readings = load_sample_data()
            print("Sample data loaded: %d households, %d readings." % (
                households, readings))
            continue
        for key, _label, func in MENU:
            if choice == key:
                try:
                    func()
                except (ValidationError, NotFoundError) as error:
                    print("Error:", error)
                break
        else:
            print("Invalid choice. Please enter 0-9.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nGoodbye.")
        sys.exit(0)
