import csv
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from collections import defaultdict
import networkx as nx

# ---------------------------------------------------------
# Load past matches
# ---------------------------------------------------------
def load_past_matches(filename="past_matches.csv"):
    past = set()
    try:
        with open(filename, newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            for row in reader:
                if len(row) >= 2:
                    a = row[0].strip()
                    b = row[1].strip()
                    past.add(tuple(sorted([a, b])))
    except FileNotFoundError:
        pass
    return past

# ---------------------------------------------------------
# Load availability
# ---------------------------------------------------------
def load_availability(csv_file):
    availability = defaultdict(list)
    local_timezones = {}

    with open(csv_file, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)

        for row in reader:
            name = row["Name"].strip()
            tag = row["TwoCharacters"].strip()
            tz_str = row["TimeZone"].strip()
            date_str = row["Date"].strip()
            start_str = row["Start"].strip()
            end_str = row["End"].strip()

            uid = f"{name}-{tag}"
            local_timezones[uid] = tz_str

            local_tz = ZoneInfo(tz_str)

            # Parse naive datetimes
            start_naive = datetime.strptime(f"{date_str} {start_str}", "%Y-%m-%d %H:%M")
            end_naive   = datetime.strptime(f"{date_str} {end_str}",   "%Y-%m-%d %H:%M")

            # Handle midnight crossing
            if end_naive <= start_naive:
                end_naive += timedelta(days=1)

            # Construct timezone-aware local datetimes (DST-safe)
            start_local = datetime(
                start_naive.year, start_naive.month, start_naive.day,
                start_naive.hour, start_naive.minute,
                tzinfo=local_tz
            )

            end_local = datetime(
                end_naive.year, end_naive.month, end_naive.day,
                end_naive.hour, end_naive.minute,
                tzinfo=local_tz
            )

            # Convert to UTC
            start_utc = start_local.astimezone(ZoneInfo("UTC"))
            end_utc   = end_local.astimezone(ZoneInfo("UTC"))

            availability[uid].append((start_utc, end_utc))

    # Sort intervals chronologically
    for uid in availability:
        availability[uid].sort()

    return availability, local_timezones

# ---------------------------------------------------------
# Overlap calculation
# ---------------------------------------------------------
def overlap_minutes(a_start, a_end, b_start, b_end):
    latest_start = max(a_start, b_start)
    earliest_end = min(a_end, b_end)
    delta = (earliest_end - latest_start).total_seconds() / 60
    return max(0, delta)

# ---------------------------------------------------------
# Get all overlap intervals
# ---------------------------------------------------------
def get_overlap_intervals(a_slots, b_slots):
    overlaps = []
    for (a_start, a_end) in a_slots:
        for (b_start, b_end) in b_slots:
            latest_start = max(a_start, b_start)
            earliest_end = min(a_end, b_end)
            if earliest_end > latest_start:
                overlaps.append((latest_start, earliest_end))
    return overlaps

# ---------------------------------------------------------
# Build weighted graph
# ---------------------------------------------------------
def build_weighted_graph(availability, past_matches, min_overlap=30):
    G = nx.Graph()
    ids = list(availability.keys())

    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a = ids[i]
            b = ids[j]

            has_overlap = False
            for (a_start, a_end) in availability[a]:
                for (b_start, b_end) in availability[b]:
                    if overlap_minutes(a_start, a_end, b_start, b_end) >= min_overlap:
                        has_overlap = True
                        break
                if has_overlap:
                    break

            if not has_overlap:
                continue

            pair = tuple(sorted([a, b]))
            weight = 1 if pair in past_matches else 0

            G.add_edge(a, b, weight=weight)

    return G

# ------------------------------------------------------------
# Core matching: min‑weight max‑cardinality matching
# ------------------------------------------------------------
def compute_pairs(G):
    return nx.min_weight_matching(G, weight="weight")

# ------------------------------------------------------------
# Trio augmentation
# ------------------------------------------------------------
def find_trios(G, pairs):
    # Normalize pairs into sorted tuples
    pair_list = [tuple(sorted(p)) for p in pairs]

    matched_nodes = set().union(*pair_list)
    all_nodes = set(G.nodes())
    unmatched = list(all_nodes - matched_nodes)

    trios = []
    used_pairs = set()

    for u in unmatched:
        # Try to attach u to an existing pair (a, b)
        for (a, b) in pair_list:
            if (a, b) in used_pairs:
                continue

            # Trio condition: u must overlap with both a and b
            if G.has_edge(u, a) and G.has_edge(u, b):
                trios.append((a, b, u))
                used_pairs.add((a, b))
                break

    # Remaining pairs are those not used in trios
    remaining_pairs = [p for p in pair_list if p not in used_pairs]

    return trios, remaining_pairs

# ------------------------------------------------------------
# 3. Full pipeline
# ------------------------------------------------------------
def match_with_trios(G):
    # Step 1: optimal pairs
    pairs = compute_pairs(G)

    # Step 2: trio augmentation
    trios, remaining_pairs = find_trios(G, pairs)

    return remaining_pairs, trios

# ---------------------------------------------------------
# Save new matches to past_matches.csv
# ---------------------------------------------------------
def save_new_matches(matching, filename="past_matches.csv"):
    normalized = [tuple(sorted([a, b])) for a, b in matching]

    existing = set()
    try:
        with open(filename, newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            for row in reader:
                if len(row) >= 2:
                    existing.add(tuple(sorted([row[0].strip(), row[1].strip()])))
    except FileNotFoundError:
        pass

    with open(filename, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        for pair in normalized:
            if pair not in existing:
                writer.writerow(pair)

# ---------------------------------------------------------
# Format overlap intervals for output
# ---------------------------------------------------------
def format_pair_overlap(a, b, availability, timezones):
    intervals = get_overlap_intervals(availability[a], availability[b])

    utc_strings = []
    local_strings = []

    tz_a = ZoneInfo(timezones[a])
    tz_b = ZoneInfo(timezones[b])

    for start, end in intervals:
        # UTC: show date only on start
        utc_strings.append(
            f"{start.strftime('%Y-%m-%d %H:%M')} → {end.strftime('%H:%M')}"
        )

        # Local times
        a_local_start = start.astimezone(tz_a)
        a_local_end   = end.astimezone(tz_a)
        b_local_start = start.astimezone(tz_b)
        b_local_end   = end.astimezone(tz_b)

        local_strings.append(
            f"{a} ({timezones[a]}): {a_local_start.strftime('%Y-%m-%d %H:%M')} → {a_local_end.strftime('%H:%M')} | "
            f"{b} ({timezones[b]}): {b_local_start.strftime('%Y-%m-%d %H:%M')} → {b_local_end.strftime('%H:%M')}"
        )

    return utc_strings, local_strings

def format_trio_overlap(a, b, c, availability, timezones):
    slots_a = availability[a]
    slots_b = availability[b]
    slots_c = availability[c]

    utc_overlaps = []

    # --- Compute triple intersections in UTC ---
    for (a_start, a_end) in slots_a:
        for (b_start, b_end) in slots_b:
            for (c_start, c_end) in slots_c:
                start = max(a_start, b_start, c_start)
                end   = min(a_end,   b_end,   c_end)
                if end > start:
                    utc_overlaps.append((start, end))

    # --- Format UTC strings ---
    utc_strings = [
        f"{s.strftime('%Y-%m-%d %H:%M')} → {e.strftime('%H:%M')}"
        for s, e in utc_overlaps
    ]

    # --- Format local strings (same style as pairs) ---
    tz_a = ZoneInfo(timezones[a])
    tz_b = ZoneInfo(timezones[b])
    tz_c = ZoneInfo(timezones[c])

    local_strings = []

    for s, e in utc_overlaps:
        a_s = s.astimezone(tz_a)
        a_e = e.astimezone(tz_a)
        b_s = s.astimezone(tz_b)
        b_e = e.astimezone(tz_b)
        c_s = s.astimezone(tz_c)
        c_e = e.astimezone(tz_c)

        local_strings.append(
            f"{a} ({timezones[a]}): {a_s.strftime('%Y-%m-%d %H:%M')} → {a_e.strftime('%H:%M')} | "
            f"{b} ({timezones[b]}): {b_s.strftime('%Y-%m-%d %H:%M')} → {b_e.strftime('%H:%M')} | "
            f"{c} ({timezones[c]}): {c_s.strftime('%Y-%m-%d %H:%M')} → {c_e.strftime('%H:%M')}"
        )

    return utc_strings, local_strings

# ---------------------------------------------------------
# Write matches.csv
# ---------------------------------------------------------
def write_matches_csv(filename, pairs, trios, availability, timezones):
    # Collect matched IDs
    matched = set()
    for a, b in pairs:
        matched.add(a)
        matched.add(b)
    for a, b, c in trios:
        matched.add(a)
        matched.add(b)
        matched.add(c)

    # Compute unmatched IDs
    unmatched = [uid for uid in availability.keys() if uid not in matched]

    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)

        # Header for pairs
        writer.writerow([
            "Person A",
            "Person B",
            "Overlaps (UTC)",
            "Overlaps (Local Times)"
        ])

        # Write pairs
        for a, b in sorted(pairs):
            utc_list, local_list = format_pair_overlap(a, b, availability, timezones)

            utc_str = "; ".join(utc_list) if utc_list else "None"
            local_str = "; ".join(local_list) if local_list else "None"

            writer.writerow([
                a,
                b,
                utc_str,
                local_str
            ])

        # Blank line before trios
        writer.writerow([])
        writer.writerow(["Person A", "Person B", "Person C", "Overlaps (UTC)", "Overlaps (Local Times)"])

        # Write trios
        for a, b, c in sorted(trios):
            utc_list, local_list = format_trio_overlap(a, b, c, availability, timezones)

            utc_str = "; ".join(utc_list) if utc_list else "None"
            local_str = "; ".join(local_list) if local_list else "None"

            writer.writerow([
                a,
                b,
                c,
                utc_str,
                local_str
            ])

        # Blank line before unmatched section
        writer.writerow([])
        writer.writerow(["Unmatched People"])
        writer.writerow(["----------------"])

        # Write unmatched people
        for uid in sorted(unmatched):
            writer.writerow([uid])

# ---------------------------------------------------------
# Main
# ---------------------------------------------------------
availability, local_timezones = load_availability("availability.csv")
past_matches = load_past_matches("past_matches.csv")

G = build_weighted_graph(availability, past_matches)
pairs, trios = match_with_trios(G)

matched = set()

# Print pairs
for a, b in pairs:
    print(f"{a} ↔ {b}")
    matched.add(a)
    matched.add(b)

# Print trios
for a, b, c in trios:
    print(f"{a} ↔ {b} ↔ {c}")
    matched.add(a)
    matched.add(b)
    matched.add(c)

print("\nUNMATCHED PEOPLE:")
print("-----------------")
for uid in availability.keys():
    if uid not in matched:
        print(uid)

save_new_matches(pairs)
write_matches_csv("matches.csv", pairs, trios, availability, local_timezones)

print("\nWrote matches.csv and updated past_matches.csv")