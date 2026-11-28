"""Local extraction and export for common Markdown itinerary layouts."""

import re


MINUTES_IN_DAY = 1440
DATE = r"(?:\d{4}[-/.])?\d{1,2}[-/.]\d{1,2}|\d{1,2}月\d{1,2}日"
CHINESE_DAY = r"第\s*[一二三四五六七八九十\d]+\s*天"
DAY = re.compile(rf"\b(?:day|d)\s*\d+\b|{CHINESE_DAY}|(?<!\d)(?:{DATE})(?!\d)", re.I)
TIME = re.compile(r"(?<![\d:])([01]?\d|2[0-3])[:：]([0-5]\d)\s*(am|pm)?(?![\d:])", re.I)
FENCE = re.compile(r"^\s*(?:```|~~~)")
DETOUR = re.compile(r"^\s*###\s+Detour options\s*$", re.I)
OPTION = re.compile(r"^\s*####\s+Option\s+\d+\s*[—–-]\s*(\S.*?)\s*$", re.I)
FIELD = re.compile(r"^\s*-\s*([^:：]+)[:：]\s*(.*?)\s*$")
FLEXIBLE = re.compile(r"^(?:flexible|可调整|弹性)\s*·\s*", re.I)
DURATION = re.compile(r"(?<![\d.-])(\d{1,3}(?:\.\d{1,2})?)\s*(hours?|hrs?\b|h\b|minutes?\b|mins?\b|小时|分钟)", re.I)
TIME_SEPARATOR = re.compile(r"\s*(?:[-–—~～]|to|至|到)\s*", re.I)
CONFIRMED_CHANGE = " · Confirmed itinerary change · "
TIME_CHANGE = re.compile(r"(?:^| · )Time change ([+−-]?\d{1,3}(?:\.\d{1,2})?) hr(?: · |$)")
DAY_NUMBERS = {
    "free minutes": ("free_minutes", 1440), "minimum rest minutes": ("minimum_rest", 1440),
    "trip budget usd": ("trip_budget", None), "original trip estimate usd": ("baseline_spend", None),
    "extra transport limit usd": ("extra_transport_cap", None),
}
OPTION_NUMBERS = {
    "visit minutes": ("visit_minutes", 1440), "extra drive minutes": ("travel_minutes", 1440),
    "extra transport usd": ("transport_cost", None), "other cost usd": ("activity_cost", None),
    "interest": ("interest", 5), "route fit": ("route_fit", 5),
    "released minutes": ("replaced_minutes", 1440),
    "recoverable original cost usd": ("replaced_activity_cost", None),
    "public rating": ("reputation", None),
}
DAY_TEXT = {
    "question": "question", "start location": "start_location", "end location": "end_location",
    "start coordinates": "start_coordinates", "end coordinates": "end_coordinates",
    "setting": "setting",
}
OPTION_TEXT = {"replace": "replaces", "rating source": "reputation_source", "notes": "notes",
               "coordinates": "coordinates", "map name": "map_name"}
IDEAS = re.compile(r"^(?:detour ideas?|optional ideas?|possible changes?|想调整|想加的地方|备选(?:方案)?)\s*[:：]\s*", re.I)
IDEA_VERB = re.compile(r"^(?:maybe add\b|can (?:we|i) add\b|(?:i|we) (?:want to|could|might) add\b|"
                       r"(?:try adding|consider adding|add)\b|(?:考虑|想要?|能不能|能否|可以|能)?加(?:上|去)?|想去)\s*", re.I)
DAY_REFERENCE = re.compile(r"^(?:on\s+)?(?:day\s*(\d+)|第\s*([一二三四五六七八九十\d]+)\s*天)\s*[,:：，·—-]?\s*", re.I)
TIME_OF_DAY = {"morning": (540, 720), "上午": (540, 720), "早上": (540, 720),
               "afternoon": (720, 1020), "下午": (720, 1020),
               "evening": (1020, 1260), "晚上": (1020, 1260), "傍晚": (1020, 1260)}
TIME_WORDS = "|".join(TIME_OF_DAY)
ENDING_DURATION = re.compile(r"\s*[,，；;(（]\s*(?:allow|about|around|for|逛|约|预计|预留)?\s*.*$", re.I)
SWAP = re.compile(r"^(?:replace (.+?) with |(?:想|能不能|能否|能)?把\s*(.+?)\s*(?:换成|改成|替换成)\s*)(.+)$", re.I)
BEFORE_CHECK_IN = r"(?:before (?:hotel )?check[ -]?in|(?:酒店)?入住前)"
CHECK_IN = re.compile(r"\bcheck[ -]?in\b|入住", re.I)


def _minutes(match):
    hour = int(match[1])
    if match[3]:
        hour = hour % 12 + (12 if match[3].lower() == "pm" else 0)
    return hour * 60 + int(match[2])


def stated_duration(text):
    match = DURATION.search(text)
    if not match:
        return None
    unit = match[2].casefold()
    minutes = round(float(match[1]) * (60 if unit.startswith("h") or unit == "小时" else 1))
    return minutes if 0 < minutes <= MINUTES_IN_DAY else None


def change_ideas(text, *, allow_names=False):
    """Read explicit change requests without interpreting other itinerary notes as instructions."""
    text = text.strip().strip("*").rstrip("?？。 ")
    day = DAY_REFERENCE.match(text)
    if day:
        text = text[day.end():]
    text, before_check_in = re.subn(rf"^{BEFORE_CHECK_IN}[\s,，]*", "", text, flags=re.I)
    period = re.match(rf"^(?:in the\s+)?({TIME_WORDS})\s*[,，:]?\s*", text, re.I)
    if not period:
        period = re.search(rf"\s+(?:(?:in|this)\s+)?(?:the\s+)?({TIME_WORDS})$", text, re.I)
    if period:
        text = (text[:period.start()] + text[period.end():]).strip()
    prefix = IDEAS.match(text) or IDEA_VERB.match(text)
    if prefix:
        text = text[prefix.end():]
    elif not allow_names and not SWAP.fullmatch(text):
        return []
    options = []
    for choice in re.split(r"\s+or\s+|或者|还是|或|\s+/\s+", text, flags=re.I):
        choice = IDEA_VERB.sub("", choice.strip()).rstrip("吗么?？。 ")
        if not choice:
            continue
        swap = SWAP.fullmatch(choice)
        if swap:
            choice = swap[3]
        choice, before_suffix = re.subn(rf"\s*{BEFORE_CHECK_IN}(?=\s*[,，(（;；]|\s*$)",
                                        "", choice, flags=re.I)
        visit = stated_duration(choice)
        if visit:
            choice = ENDING_DURATION.sub("", choice)
            choice = re.sub(r"\s*(?:，|,)?\s*(?:for|逛|约|预计|预留)\s*$", "", DURATION.split(choice, maxsplit=1)[0], flags=re.I)
        name = choice.strip(" ,，:：-·*（(\t")
        if name and len(name) <= 120:
            option = {"name": name}
            if swap:
                option["replaces"] = (swap[1] or swap[2]).strip()
            if visit:
                option["visit_minutes"] = visit
            if period:
                option["window"] = TIME_OF_DAY[period[1].casefold()]
            if before_check_in or before_suffix:
                option["before_check_in"] = True
            options.append(option)
    return options


def requested_day(text):
    match = DAY_REFERENCE.match(text.strip())
    if match:
        number = match[1] or match[2]
        if number.isdecimal():
            return int(number)
        digits = {number: index for index, number in enumerate("一二三四五六七八九十", 1)}
        if number in digits:
            return digits[number]
        if "十" in number:
            left, right = number.split("十", 1)
            if left in digits or not left:
                return 10 * digits.get(left, 1) + digits.get(right, 0)
    return None


def _detour_field(target, text, numbers, labels):
    match = FIELD.match(text)
    if not match or not match[2]:
        return
    label, value = match[1].strip().casefold(), match[2]
    if label in labels:
        target[labels[label]] = value
    elif label == "start" and TIME.fullmatch(value):
        target["start"] = _minutes(TIME.fullmatch(value))
    elif label == "luggage" and value.casefold() in ("yes", "no"):
        target["luggage"] = value.casefold() == "yes"
    elif label in numbers and re.fullmatch(r"\d+(?:\.\d{1,2})?", value):
        key, maximum = numbers[label]
        if maximum is not None and value.isascii() and value.isdigit() and len(value) <= 4:
            number = int(value)
            if number <= maximum and (key not in ("route_fit", "interest") or number >= 1):
                target[key] = number
        elif maximum is None and len(value) <= 14:
            number = float(value)
            if key != "reputation" or 1 <= number <= 5:
                target[key] = number


def find_flexible_stop(items, name):
    """Match either an exact bullet or its short activity name."""
    if not name:
        return None
    normalized = name.strip().casefold()
    matches = []
    for item in items:
        if item["start"] is not None:
            continue
        title = FLEXIBLE.sub("", item["text"]).split(" · ", 1)[0]
        if normalized in (item["text"].casefold(), title.casefold()):
            matches.append(item)
    return matches[0] if len(matches) == 1 else None


def parse_itinerary(markdown):
    """Extract day sections and activity lines, leaving unknown fields unknown."""
    days = []
    fenced = False
    lines = markdown.splitlines()
    detour = option = None
    for index, line in enumerate(lines):
        if FENCE.match(line):
            fenced = not fenced
            continue
        if fenced:
            continue
        if detour is not None:
            option_heading = OPTION.match(line)
            if option_heading:
                option = {"name": option_heading[1]}
                detour["candidates"].append(option)
                continue
            if re.match(r"^\s*#{1,3}\s+", line):
                detour["end_line"] = index
                detour = option = None
            else:
                if option is None:
                    _detour_field(detour["settings"], line, DAY_NUMBERS, DAY_TEXT)
                else:
                    _detour_field(option, line, OPTION_NUMBERS, OPTION_TEXT)
                continue
        if days and DETOUR.match(line):
            detour = {"start_line": index, "end_line": len(lines), "settings": {}, "candidates": []}
            days[-1]["detour"] = detour
            continue
        clean = re.sub(r"^\s*(?:#{1,6}\s+|[-*+]\s+|\d+\.\s+)", "", line).strip().strip("*")
        heading = re.match(r"^\s*#{1,6}\s+", line)
        plain_day = (not re.match(r"^\s*(?:[-*+]|>)\s+", line) and
                     re.match(rf"^(?:day\s+\d+|d\d+|{CHINESE_DAY}|(?:{DATE}))(?:\s|[：:｜|—–-]|$)", clean, re.I))
        if plain_day or (heading and DAY.search(clean)):
            days.append({"title": clean, "line": index, "items": []})
            continue
        if not days or not clean or heading:
            continue
        day = days[-1]
        idea = change_ideas(clean.removeprefix(">").strip())
        if idea:
            day.setdefault("ideas", []).extend(idea)
            day.setdefault("idea_lines", []).append(index)
            continue
        if clean.startswith(">"):
            continue
        settings = {}
        _detour_field(settings, "- " + clean, DAY_NUMBERS, DAY_TEXT)
        if settings:
            day.setdefault("settings", {}).update(settings)
            continue
        table_row = clean.startswith("|")
        if table_row:
            columns = [cell.strip() for cell in clean.strip("|").split("|")]
            if not any(TIME.search(column) for column in columns) and not re.fullmatch(r"(?i)morning|afternoon|evening|上午|下午|晚上", columns[0]):
                continue
            clean = " · ".join(column for column in columns if column)
        if not (table_row or TIME.search(clean) or re.match(r"^\s*(?:[-*+]\s+|\d+\.\s+)", line)):
            continue
        activity, confirmed, details = clean.partition(CONFIRMED_CHANGE)
        flexible = bool(FLEXIBLE.match(activity))
        times = list(TIME.finditer(activity))
        start = _minutes(times[0]) if times else None
        end = None
        if len(times) > 1 and TIME_SEPARATOR.fullmatch(clean[times[0].end():times[1].start()]):
            possible_end = _minutes(times[1])
            if possible_end > start:
                end = possible_end
        item = {"text": clean, "start": None if flexible else start,
                "end": None if flexible else end, "line": index}
        if confirmed:
            item["confirmed_activity"] = activity
            change = TIME_CHANGE.search(details)
            if change:
                item["time_change"] = round(60 * float(change[1].replace("−", "-")))
        if flexible:
            item["flexible"] = True
            if end is not None:
                item.update(flexible_start=start, flexible_end=end, duration=end - start)
        if item["start"] is None and "duration" not in item:
            item["duration"] = stated_duration(activity)
        day["items"].append(item)
    return days


def format_hours(minutes):
    hours = f"{minutes / 60:.2f}".rstrip("0").rstrip(".")
    return f"{hours} hr"


def place_name(activity):
    """Read the place portion of a timed activity for a map lookup."""
    label = re.sub(r"^\d{1,2}[:：]\d{2}(?:\s*(?:[-–—~～]|to|至|到)\s*\d{1,2}[:：]\d{2})?\s*",
                   "", activity["text"], flags=re.I)
    label = label.split(" · ", 1)[0]
    label = re.sub(r"^(?:(?:hotel )?check[ -]?in(?: at|:)?\s*|入住\s*)", "", label, flags=re.I)
    return label.strip()


def drive_anchors(day, idea):
    """Use adjacent activities and require a unique check-in for a before-check-in request."""
    items = day["items"]
    if idea.get("before_check_in"):
        matches = [index for index, item in enumerate(items)
                   if item["start"] is not None and CHECK_IN.search(item["text"])]
        if len(matches) == 1 and matches[0] > 0:
            index = matches[0]
            return items[index - 1], items[index]
    elif len(items) == 2 and all(item["start"] is not None for item in items):
        return tuple(items)
    return None


def export_itinerary(markdown, days, day_index, row, accepted):
    """Keep the original itinerary and add the user's decision to its day."""
    if not accepted:
        return markdown
    lines = markdown.split("\n")
    next_index = day_index + 1
    day_end = days[next_index]["line"] if next_index < len(days) else len(lines)
    name = row["label"].replace("\n", " ")
    cost = f'{"−" if row["extra_cost"] < 0 else "+"}${abs(row["extra_cost"]):.2f}'
    start, end = int(row["start"]), int(row["end"])
    slot = f'{start // 60:02d}:{start % 60:02d}–{end // 60:02d}:{end % 60:02d}'
    day = days[day_index]
    removed = set(day.get("idea_lines", []))
    insert_at = next((item["line"] for item in day["items"]
                      if item["start"] is not None and item["start"] > start), day_end)
    if day.get("detour"):
        removed.update(range(day["detour"]["start_line"], day["detour"]["end_line"]))
        insert_at = min(insert_at, day["detour"]["start_line"])
    if row.get("replaces"):
        original = find_flexible_stop(day["items"], row["replaces"])
        if original is None:
            raise ValueError("The original flexible stop could not be found.")
        removed.add(original["line"])
    details = [f'Budget change {cost}', f'Time change {format_hours(row["extra_minutes"])}']
    if row.get("transport_mode"):
        details.append(row["transport"])
    block = [f"- {slot} {name}{CONFIRMED_CHANGE}Including travel · " + " · ".join(details)]
    if row["notes"]:
        block.extend(f"> Check {note}" for note in row["notes"])
    before = [line for index, line in enumerate(lines[:insert_at]) if index not in removed]
    after = [line for index, line in enumerate(lines[insert_at:], start=insert_at) if index not in removed]
    return "\n".join([*before, *block, "", *after])


def export_trip(original, decisions):
    """Apply each confirmed day to the original file. An absent day keeps its original content."""
    days = parse_itinerary(original)
    markdown = original
    for index, decision in sorted(decisions.items(), reverse=True):
        if decision["action"] == "accept":
            markdown = export_itinerary(markdown, days, index, decision["row"], accepted=True)
    return markdown
