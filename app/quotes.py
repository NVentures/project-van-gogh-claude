#!/usr/bin/env python3
"""The Stoic quote that opens Afternoon Tea.

WHY THIS IS CODE AND NOT A PROMPT
The quote used to be a list of eight in the skill file with the instruction
"pick one at random". That repeated for two independent reasons, and only one
of them is about the size of the list. A model asked to pick randomly has no
memory of yesterday's briefing and biases hard toward the head of a list, so
the same quote came back within days. Randomness with no state is not
rotation. The pool and the selection are therefore both moved here, where a
recency ledger can actually enforce "not this one again".

WHERE THE QUOTES COME FROM
All 134 are compiled in, hand-picked from roughly 500 harvested off
stoic-quotes.com. Nothing is fetched at briefing time: the network is off by
default, so no briefing depends on a third party being up, and no unscreened
quote can reach the reader. Refreshing the list is a deliberate act by a
person, not something that happens on its own at 6am.

WHAT THE FILTER IS FOR
The upstream pool is not curated for this use. Sampling it returns Latin with
a parenthetical translation, and stage dialogue from Seneca's tragedies
("Theseus: What is the crime ... Phaedra: My life."). Both are real Stoic
text and neither opens a business briefing. `is_usable` drops those shapes on
the way into the cache, so a bad row is rejected once at fetch rather than
surviving in the cache to be picked later.
"""

import json
import os
import random
import re
from datetime import datetime, timezone
from pathlib import Path

import requests

# Bounded like every other outbound call in the briefing path. This runs
# unattended; a hung socket costs the whole retro, and the fallback below is
# always available, so waiting is never the better trade.
TIMEOUT = 6

# One quote, and a random ten. The bulk endpoint is what makes the cache
# worth having: one call seeds ten days.
QUOTE_URL = "https://stoic-quotes.com/api/quote"
BULK_URL = "https://stoic-quotes.com/api/quotes"

# How many recently shown quotes to remember. The ledger is the whole point
# of the module: with a cache larger than this, a quote cannot return until
# thirty briefings have passed.
RECENT_MEMORY = 30

# Refill when the cache holds fewer unseen quotes than this. Chosen so the
# network is touched rarely (roughly every ten briefings) rather than daily.
REFILL_THRESHOLD = 5

# Cache ceiling. Without a cap the file grows for the life of the install.
MAX_CACHE = 200

# Seneca argues for suicide as a route to freedom in several letters, and the
# passages are short, calm and structurally indistinguishable from advice
# ("You need only turn over your wrists"). Nothing about their grammar marks
# them, so this is the one filter that has to name its subject. It is
# deliberately narrow: it matches the act, not the words "death" or "die",
# which are ordinary Stoic vocabulary and appear throughout the good pool.
_SELF_HARM = re.compile(
    r"turn over your wrists|open(?:ing)? (?:your|the) veins|"
    r"cut (?:your|the) (?:throat|veins|wrists)|"
    r"(?:kill|slay|destroy) (?:yourself|thyself)|"
    r"take your own life|by your own hand",
    re.IGNORECASE)

# A quote longer than this is a paragraph, and a briefing opens with a line.
# 140 is measured, not guessed: over a 113-quote sample of the live pool the
# median is 96 characters and 20 rows run past 140, and every one of those
# reads as an excerpt rather than an aphorism. The longest also carried the
# sample's only garbled passage ("according to the paristi register"), which
# no punctuation or grammar check would catch: upstream OCR damage correlates
# with length, so the cap removes it as a side effect rather than by naming it.
MAX_LEN = 140
MIN_LEN = 20

# Stage dialogue: "Theseus: What is the crime ... Phaedra: My life." A capital
# name followed by a colon, twice, is a script and not an aphorism.
_DIALOGUE = re.compile(r"\b[A-Z][a-z]+:\s")

# Latin with a parenthetical translation. The tell is a long parenthetical
# carrying its own sentence, which reads as an editorial footnote.
_TRANSLATION = re.compile(r"\([^)]{40,}\)")

# A handle is an author field from a content farm, not one of the Stoics.
_HANDLE = re.compile(r"[@#]")

# Editorial debris carried over from the scanned book: a trailing footnote
# marker ("...devote our energy to.  4.") or an appended chapter title
# ("...an act of courage.<curly>   Anger."). Both sit AFTER the sentence has
# already ended, which is what distinguishes them from ordinary text.
_TRAILING_DEBRIS = re.compile(
    r"[.!?][\u201d\u2019\"']?\s+(?:\d+\.?|[A-Z][a-z]+\.?)\s*$")

# A curly quotation mark is only ever a quotation mark (unlike the straight
# apostrophe, which doubles as one inside words), so an odd count of the
# closing form is an unambiguous fragment.
_CURLY_CLOSE = "\u201d"

# A doubled word ("the inweaving and and enfolding") is a transcription slip in
# the upstream text. It is invisible to a length or punctuation check and it is
# the kind of thing a reader notices immediately.
_DOUBLED = re.compile(r"\b(\w+)\s+\1\b", re.I)

# An orphan quote mark: the row is a fragment cut out of a longer passage, so it
# opens or closes a quotation that is not there ("A poor soul burdened with a
# corpse,' Epictetus calls you."). Apostrophes inside words are not counted,
# because "men's" and "won't" are ordinary text.
_APOSTROPHE_IN_WORD = re.compile(r"(?<=\w)['\u2019](?=\w)")

# The pool the module falls back to, and the pool it started as. These are
# kept because they are known good: short, attributed, and readable cold.
# THE POOL. Hand-picked, one at a time, from roughly 500 quotes pulled off the
# upstream API. The API is a fine library and a poor editor: about a quarter of
# what it serves is unsuitable for opening a working day, and the reasons are
# not things a pattern can catch. "Stupidity is expecting figs in winter, or
# children in old age" is correctly attributed, correctly punctuated, and
# structurally perfect. It is simply a strange thing to read at 6am.
#
# So tone is settled here, by reading, rather than at runtime by a filter.
# Rejected on the way in: anything morbid, anything scolding, anything needing
# outside context (Pythocles, Lucilius, the gladiator adage), archaic
# thee/thou phrasing, fragments that trail off, and near-duplicate
# translations of a line already in the list.
#
# The structural filter below still runs over these, so a bad edit here cannot
# ship, and a test asserts every entry passes it.
CURATED = [
    {"text": "You have power over your mind, not outside events. Realize this, and you will find strength.",
     "author": "Marcus Aurelius"},
    {"text": "The impediment to action advances action. What stands in the way becomes the way.",
     "author": "Marcus Aurelius"},
    {"text": "We cannot choose our external circumstances, but we can always choose how we respond to them.",
     "author": "Epictetus"},
    {"text": "Circumstances don't make the man, they only reveal him to himself.",
     "author": "Epictetus"},
    {"text": "Men are not afraid of things, but of how they view them.",
     "author": "Epictetus"},
    {"text": "What upsets people is not things themselves but their judgements about these things.",
     "author": "Epictetus"},
    {"text": "If you are distressed about anything, the pain is not due to the thing but to your own estimate of it.",
     "author": "Marcus Aurelius"},
    {"text": "Man is troubled not by events, but by the meaning he gives to them.",
     "author": "Epictetus"},
    {"text": "Some things are up to us, and some things are not up to us.",
     "author": "Epictetus"},
    {"text": "The more we value things outside our control, the less control we have.",
     "author": "Epictetus"},
    {"text": "Always remember what is your own and what is not, and you'll never be troubled.",
     "author": "Epictetus"},
    {"text": "Who then is invincible? The one who cannot be upset by anything outside their reasoned choice.",
     "author": "Epictetus"},
    {"text": "Nothing that goes on in anyone else's mind can harm you.",
     "author": "Marcus Aurelius"},
    {"text": "Where is harm to be found? In your capacity to see it. Stop doing that and everything will be fine.",
     "author": "Marcus Aurelius"},
    {"text": "Do not suppose you are hurt, and your complaint ceases; cease your complaint, and you are not hurt.",
     "author": "Marcus Aurelius"},
    {"text": "Reject your sense of injury and the injury itself disappears.",
     "author": "Marcus Aurelius"},
    {"text": "Fire is the test of gold; adversity, of strong men.",
     "author": "Seneca"},
    {"text": "Brave men rejoice in adversity, just as brave soldiers triumph in war.",
     "author": "Seneca"},
    {"text": "The bravest sight in the world is to see a great man struggling against adversity.",
     "author": "Seneca"},
    {"text": "No man is more unhappy than he who never faces adversity, for he is not permitted to prove himself.",
     "author": "Seneca"},
    {"text": "The thing itself was no misfortune at all; to endure it and prevail is great good fortune.",
     "author": "Marcus Aurelius"},
    {"text": "No condition is so distressing that a balanced mind cannot find some comfort in it.",
     "author": "Seneca"},
    {"text": "Nothing happens to any man that he is not formed by nature to bear.",
     "author": "Marcus Aurelius"},
    {"text": "It is not because things are difficult that we do not dare, it is because we do not dare that they are difficult.",
     "author": "Seneca"},
    {"text": "Because a thing seems difficult for you, do not think it impossible for anyone to accomplish.",
     "author": "Marcus Aurelius"},
    {"text": "Difficulties strengthen the mind, as labor does the body.",
     "author": "Seneca"},
    {"text": "We suffer more often in imagination than in reality.",
     "author": "Seneca"},
    {"text": "There are more things to alarm us than to harm us, and we suffer more often in apprehension than reality.",
     "author": "Seneca"},
    {"text": "The greatest obstacle to living is expectancy, which hangs upon tomorrow and loses today.",
     "author": "Seneca"},
    {"text": "True happiness is to enjoy the present, without anxious dependence upon the future.",
     "author": "Seneca"},
    {"text": "Confine yourself to the present.",
     "author": "Marcus Aurelius"},
    {"text": "He suffers more than necessary, who suffers before it is necessary.",
     "author": "Seneca"},
    {"text": "What's the good of dragging up sufferings which are over, of being unhappy now just because you were then?",
     "author": "Seneca"},
    {"text": "Waste no more time arguing what a good man should be. Be one.",
     "author": "Marcus Aurelius"},
    {"text": "No more roundabout discussions of what makes a good man. Be one!",
     "author": "Marcus Aurelius"},
    {"text": "First say to yourself what you would be, and then do what you have to do.",
     "author": "Epictetus"},
    {"text": "Tell yourself what you want to be, then act your part accordingly.",
     "author": "Epictetus"},
    {"text": "A person's worth is measured by the worth of what he values.",
     "author": "Marcus Aurelius"},
    {"text": "Bear in mind that the measure of a man is the worth of the things he cares about.",
     "author": "Marcus Aurelius"},
    {"text": "Never esteem anything as of advantage to you that will make you break your word or lose your self-respect.",
     "author": "Marcus Aurelius"},
    {"text": "The best kind of revenge is not to become like them.",
     "author": "Marcus Aurelius"},
    {"text": "Whatever anyone does or says, I must be emerald and keep my colour.",
     "author": "Marcus Aurelius"},
    {"text": "Does the emerald lose its beauty for lack of admiration?",
     "author": "Marcus Aurelius"},
    {"text": "A good man does not spy around for the black spots in others, but presses unswervingly on towards his mark.",
     "author": "Marcus Aurelius"},
    {"text": "Know, first, who you are, and then adorn yourself accordingly.",
     "author": "Epictetus"},
    {"text": "No man's good by accident. Virtue has to be learnt.",
     "author": "Seneca"},
    {"text": "If you're honest and straightforward and mean well, it should show in your eyes. It should be unmistakable.",
     "author": "Marcus Aurelius"},
    {"text": "A wise man never asks what another man serves, for only his actions will speak the truth.",
     "author": "Seneca"},
    {"text": "A man who makes a decision without listening to both sides is unjust, even if his ruling is a fair one.",
     "author": "Seneca"},
    {"text": "The happiness of your life depends upon the quality of your thoughts.",
     "author": "Marcus Aurelius"},
    {"text": "The soul becomes dyed with the colours of its thoughts.",
     "author": "Marcus Aurelius"},
    {"text": "Your character is simply the sum of your thoughts over time.",
     "author": "Marcus Aurelius"},
    {"text": "A man's life is dyed the colour of his imagination.",
     "author": "Marcus Aurelius"},
    {"text": "The mind is never right but when it is at peace with itself.",
     "author": "Seneca"},
    {"text": "The more a mind takes in the more it expands.",
     "author": "Seneca"},
    {"text": "It is impossible for a man to learn what he thinks he already knows.",
     "author": "Epictetus"},
    {"text": "Everyone prefers belief to the exercise of judgement.",
     "author": "Seneca"},
    {"text": "Greatness of reason is measured not by height or length, but by the quality of its judgements.",
     "author": "Epictetus"},
    {"text": "It is not that we have a short time to live but that we waste a lot of it.",
     "author": "Seneca"},
    {"text": "We are not given a short life but we make it short. Life is long if you know how to use it.",
     "author": "Seneca"},
    {"text": "Begin at once to live, and count each separate day as a separate life.",
     "author": "Seneca"},
    {"text": "Lay hold of today's task, and you will not need to depend so much upon tomorrow's.",
     "author": "Seneca"},
    {"text": "The whole future lies in uncertainty: live immediately.",
     "author": "Seneca"},
    {"text": "Life is long and there is enough of it for satisfying personal accomplishments if we use our hours well.",
     "author": "Seneca"},
    {"text": "Do every act of your life as though it were the very last act of your life.",
     "author": "Marcus Aurelius"},
    {"text": "Life is like a play: it's not the length, but the excellence of the acting that matters.",
     "author": "Seneca"},
    {"text": "How late it is to begin living only when one must stop!",
     "author": "Seneca"},
    {"text": "Give your heart to the trade you have learnt, and draw refreshment from it.",
     "author": "Marcus Aurelius"},
    {"text": "Let no act be done without a purpose.",
     "author": "Marcus Aurelius"},
    {"text": "Even the least of our activities ought to have some end in view.",
     "author": "Marcus Aurelius"},
    {"text": "On every occasion a man should ask himself, is this one of the unnecessary things?",
     "author": "Marcus Aurelius"},
    {"text": "Be not either a man of many words, or busy about too many things.",
     "author": "Marcus Aurelius"},
    {"text": "To be everywhere is to be nowhere.",
     "author": "Seneca"},
    {"text": "It takes you more time to solve a problem than to set it.",
     "author": "Seneca"},
    {"text": "Nothing hinders a cure so much as frequent changes of treatment.",
     "author": "Seneca"},
    {"text": "Luck is what happens when preparation meets opportunity.",
     "author": "Seneca"},
    {"text": "A ship should not ride on a single anchor, nor life on a single hope.",
     "author": "Epictetus"},
    {"text": "If a man knows not to which port he sails, no wind is favorable.",
     "author": "Seneca"},
    {"text": "When you find your direction, check to make sure that it is the right one.",
     "author": "Epictetus"},
    {"text": "Wealth consists not in having great possessions, but in having few wants.",
     "author": "Epictetus"},
    {"text": "Nothing satisfies greed, but even a little satisfies nature.",
     "author": "Seneca"},
    {"text": "He who needs riches least, enjoys riches most.",
     "author": "Seneca"},
    {"text": "Remember that very little is needed to make a happy life.",
     "author": "Marcus Aurelius"},
    {"text": "Very little is needed to make a happy life; it is all within yourself in your way of thinking.",
     "author": "Marcus Aurelius"},
    {"text": "If you live according to nature, you will never be poor; if you live according to opinion, you will never be rich.",
     "author": "Seneca"},
    {"text": "Do you ask what is the proper limit to wealth? It is, first, to have what is necessary, and, second, to have what is enough.",
     "author": "Seneca"},
    {"text": "The acquisition of riches has been for many men, not an end, but a change, of troubles.",
     "author": "Seneca"},
    {"text": "Freedom is not achieved by satisfying desire, but by eliminating it.",
     "author": "Epictetus"},
    {"text": "If you wish to have pleasure for ever, do not add to your pleasures, but subtract from your desires.",
     "author": "Seneca"},
    {"text": "Fortify yourself with contentment for this is an impregnable fortress.",
     "author": "Epictetus"},
    {"text": "Seek not the good in external things; seek it in yourselves.",
     "author": "Epictetus"},
    {"text": "Wherever there is a human being, there is an opportunity for a kindness.",
     "author": "Seneca"},
    {"text": "Men exist for the sake of one another. Teach them then or bear with them.",
     "author": "Marcus Aurelius"},
    {"text": "Associate with people who are likely to improve you.",
     "author": "Seneca"},
    {"text": "Regard a friend as loyal, and you will make him loyal.",
     "author": "Seneca"},
    {"text": "Nothing is more honorable than a grateful heart.",
     "author": "Seneca"},
    {"text": "We have two ears and one mouth so that we can listen twice as much as we speak.",
     "author": "Epictetus"},
    {"text": "We do not need many words, but, rather, effective words.",
     "author": "Seneca"},
    {"text": "It's silly to try to escape other people's faults. They are inescapable. Just try to escape your own.",
     "author": "Marcus Aurelius"},
    {"text": "When you are offended at any man's fault, immediately turn to yourself and reflect in what manner you yourself have erred.",
     "author": "Marcus Aurelius"},
    {"text": "No one becomes a laughingstock who laughs at himself.",
     "author": "Seneca"},
    {"text": "Most powerful is he who has himself in his own power.",
     "author": "Seneca"},
    {"text": "The nearer a man comes to a calm mind, the closer he is to strength.",
     "author": "Marcus Aurelius"},
    {"text": "Nowhere can man find a quieter or more untroubled retreat than in his own soul.",
     "author": "Marcus Aurelius"},
    {"text": "Tranquillity is nothing else than the good ordering of the mind.",
     "author": "Marcus Aurelius"},
    {"text": "How much more grievous are the consequences of anger than the causes of it.",
     "author": "Marcus Aurelius"},
    {"text": "The road is long if one proceeds by way of precepts but short and effectual if by way of personal example.",
     "author": "Seneca"},
    {"text": "What progress, you ask, have I made? I have begun to be a friend to myself.",
     "author": "Seneca"},
    {"text": "No man is despised by another unless he is first despised by himself.",
     "author": "Seneca"},
    {"text": "Loss is nothing else but change, and change is Nature's delight.",
     "author": "Marcus Aurelius"},
    {"text": "The universe is change; life is your perception of it.",
     "author": "Marcus Aurelius"},
    {"text": "We shrink from change; yet is there anything that can come into being without it?",
     "author": "Marcus Aurelius"},
    {"text": "In times of happiness, no point in shaking things up. But in a time of crisis, the safest thing is change.",
     "author": "Seneca"},
    {"text": "Every new beginning comes from some other beginning's end.",
     "author": "Seneca"},
    {"text": "Fate leads the willing and drags along the reluctant.",
     "author": "Seneca"},
    {"text": "Demand not that things happen as you wish, but wish them to happen as they do, and you will go on well.",
     "author": "Epictetus"},
    {"text": "Accept the things to which fate binds you, and love the people with whom fate brings you together, but do so with all your heart.",
     "author": "Marcus Aurelius"},
    {"text": "Everything that happens, happens as it should, and if you observe carefully, you will find this to be so.",
     "author": "Marcus Aurelius"},
    {"text": "How ridiculous and how strange to be surprised at anything which happens in life.",
     "author": "Marcus Aurelius"},
    {"text": "Never say about anything, I have lost it, but only I have given it back.",
     "author": "Epictetus"},
    {"text": "Dwell on the beauty of life. Watch the stars, and see yourself running with them.",
     "author": "Marcus Aurelius"},
    {"text": "When you arise in the morning think of what a privilege it is to be alive, to think, to enjoy, to love.",
     "author": "Marcus Aurelius"},
    {"text": "There is no easy way from the earth to the stars.",
     "author": "Seneca"},
    {"text": "Every living organism is fulfilled when it follows the right path for its own nature.",
     "author": "Marcus Aurelius"},
    {"text": "What is not good for the hive is no good for the bee.",
     "author": "Marcus Aurelius"},
    {"text": "Read good books many times, rather than many books.",
     "author": "Seneca"},
    {"text": "Hang on to your youthful enthusiasms, you'll be able to use them better when you're older.",
     "author": "Seneca"},
    {"text": "Try to enjoy the great festival of life with other men.",
     "author": "Epictetus"},
    {"text": "Is this what I was created for? To huddle under the blankets and stay warm?",
     "author": "Marcus Aurelius"},
    {"text": "Small-minded people blame others. Average people blame themselves. The wise see all blame as foolishness.",
     "author": "Epictetus"},
    {"text": "A grey-haired wrinkled man has not necessarily lived long. More accurately, he has existed long.",
     "author": "Seneca"},
    {"text": "Enjoy present pleasures in such a way as not to injure future ones.",
     "author": "Seneca"},
    {"text": "If you would escape your troubles, you need not another place but another personality.",
     "author": "Seneca"},
    {"text": "You must lay aside the burdens of the mind; until you do this, no place will satisfy you.",
     "author": "Seneca"},
]

# The historical name, kept so nothing downstream has to change.
BUILTIN = CURATED


def _strip_dashes(text: str) -> str:
    """Remove em and en dashes from anything rendered to a person.

    Same guarantee as travel.py: the interface voice rules ban them, and this
    text arrives from a third party we do not control, so the strip happens in
    code at the point it enters the cache rather than in a review pass.
    """
    # Escapes, not literals, so a dash sweep over this file cannot rewrite the
    # very characters the strip removes.
    out = text.replace("\u2014", ", ").replace("\u2013", "-")
    # A SPACED dash ("law <em> have no illusion") becomes ", " next to the
    # space that was already there, leaving "law ,  have". Collapsing runs is
    # part of the strip, not a separate tidy: the replacement is what created
    # the whitespace, so the same function has to clean up after itself.
    out = re.sub(r"\s+,", ",", out)
    return re.sub(r"\s{2,}", " ", out).strip()


def state_path() -> Path:
    """`~/.config/van-gogh/quotes.json` (honours VAN_GOGH_STATE_DIR)."""
    override = os.environ.get("VAN_GOGH_STATE_DIR")
    base = Path(override).expanduser() if override else Path.home() / ".config" / "van-gogh"
    return base / "quotes.json"


def is_usable(quote: dict) -> bool:
    """True when a fetched row reads as a briefing opener.

    Rejects the shapes sampling the live endpoint actually returned: stage
    dialogue, Latin with a parenthetical translation, paragraph-length
    passages, blank authors, and handles in the author field.
    """
    if not isinstance(quote, dict):
        return False
    text = str(quote.get("text") or "").strip()
    author = str(quote.get("author") or "").strip()
    if not text or not author:
        return False
    if not (MIN_LEN <= len(text) <= MAX_LEN):
        return False
    if _HANDLE.search(author) or len(author) > 40:
        return False
    if _DIALOGUE.search(text) or _TRANSLATION.search(text):
        return False
    # Scanned headings survive as all-caps ("HE WHO ACTS UNJUSTLY ACTS
    # IMPIOUSLY"), which reads as shouting. Measured on the letters only, so
    # punctuation and digits cannot tip a normal sentence over.
    letters = [c for c in text if c.isalpha()]
    if letters and sum(c.isupper() for c in letters) / len(letters) > 0.6:
        return False
    if _DOUBLED.search(text) or _TRAILING_DEBRIS.search(text):
        return False
    if _SELF_HARM.search(text):
        return False
    if text.count(_CURLY_CLOSE) % 2:
        return False
    # A fragment cut from a longer passage carries an unmatched quote mark.
    bare = _APOSTROPHE_IN_WORD.sub("", text)
    for mark in ('"', "'", "\u2019", "\u201c"):
        if bare.count(mark) % 2:
            return False
    if text.count('"') > 2 or "[" in text:
        return False
    return True


def normalize(quote: dict) -> dict:
    """A fetched row reduced to the two fields the briefing renders."""
    return {
        "text": _strip_dashes(str(quote.get("text") or "").strip()),
        "author": _strip_dashes(str(quote.get("author") or "").strip()),
    }


def load_state() -> dict:
    """The cache and the recency ledger, or empty scaffolding."""
    path = state_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:                                           # noqa: BLE001
        return {"cache": [], "recent": []}
    if not isinstance(data, dict):
        return {"cache": [], "recent": []}
    cache = data.get("cache")
    recent = data.get("recent")
    return {
        "cache": cache if isinstance(cache, list) else [],
        "recent": recent if isinstance(recent, list) else [],
    }


def save_state(state: dict) -> None:
    """Write the cache. Never raises: a quote is not worth a failed briefing."""
    try:
        path = state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        tmp.replace(path)
    except Exception:                                           # noqa: BLE001
        pass


def fetch(bulk: bool = True) -> list:
    """Quotes from stoic-quotes.com, filtered. [] on any failure.

    The endpoint is unauthenticated and has no uptime guarantee, so every
    error path here returns the empty list and lets the cache carry the day.
    """
    url = BULK_URL if bulk else QUOTE_URL
    try:
        resp = requests.get(url, timeout=TIMEOUT)
        resp.raise_for_status()
        payload = resp.json()
    except Exception:                                           # noqa: BLE001
        return []
    rows = payload if isinstance(payload, list) else [payload]
    out = []
    for row in rows:
        if is_usable(row):
            out.append(normalize(row))
    return out


def _unseen(cache: list, recent: list) -> list:
    """Cache entries whose text is not in the recency ledger."""
    seen = set(recent)
    return [q for q in cache if q.get("text") not in seen]


def pick(allow_network: bool = False):
    """Return `(quote, commit)`: the quote to render, and how to record it.

    The network is OFF by default. The curated pool above is the product;
    fetching would reintroduce exactly the unscreened quotes it was built to
    exclude. `allow_network=True` still works for harvesting candidates when
    the list is next revisited, which is the only thing it is for now.

    The commit is deferred on purpose, the same way travel.py defers its
    notice ledger. "Recently shown" must mean the reader actually saw it, so
    the ledger is only written after the briefing is printed. Marking at
    selection time would burn a quote on a run that crashed before rendering.
    """
    state = load_state()
    # A cache written by an earlier version holds quotes that never went
    # through curation, and on this machine that was 17 of 19. Left alone they
    # would quietly dilute the curated pool, which is the whole point of it,
    # and the reader would go on seeing the odd ones. Curation is a membership
    # test, not just a filter, so anything not on the list is dropped on read.
    approved = {q["text"] for q in CURATED}
    cache = [q for q in state["cache"] if q.get("text") in approved and is_usable(q)]
    recent = [t for t in state["recent"] if isinstance(t, str)]

    # Refill when the unseen pool runs low, not on every run. A cache that
    # only ever holds what was fetched today would defeat the ledger.
    if allow_network and len(_unseen(cache, recent)) < REFILL_THRESHOLD:
        known = {q["text"] for q in cache}
        for q in fetch(bulk=True):
            if q["text"] not in known:
                cache.append(q)
                known.add(q["text"])
        cache = cache[-MAX_CACHE:]

    pool = _unseen(cache, recent)
    if not pool:
        # Everything cached has been shown recently. Prefer the builtins that
        # are themselves unseen before giving up and allowing a repeat.
        pool = _unseen(BUILTIN, recent)
    if not pool:
        # Every quote we hold is in the ledger. Showing the oldest is the
        # least-recently-seen choice available, and is still better than
        # opening the briefing with nothing.
        pool = cache or list(BUILTIN)
        oldest = {t: i for i, t in enumerate(recent)}
        pool = sorted(pool, key=lambda q: oldest.get(q.get("text"), -1))[:1]

    quote = random.choice(pool)

    def commit() -> None:
        """Record the quote as shown. Called after the briefing prints."""
        ledger = [t for t in recent if t != quote["text"]]
        ledger.append(quote["text"])
        save_state({"cache": cache, "recent": ledger[-RECENT_MEMORY:],
                    "updated": datetime.now(timezone.utc).isoformat()})

    return quote, commit


def render(quote: dict) -> str:
    """The quote as the briefing prints it: a blockquote, author unpunctuated.

    Matches the shipped afternoon-tea.md exactly. The author follows the closing
    quotation mark with a plain space, because the dash that would conventionally
    sit there is banned by the interface voice rules.
    """
    return f'> "{quote["text"]}" {quote["author"]}'


def main() -> int:
    quote, commit = pick()
    print(json.dumps({"quote": quote, "quote_md": render(quote)}, indent=2))
    commit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
