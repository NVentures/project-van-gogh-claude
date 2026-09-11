"""quotes.py tests.

The load-bearing group is "no repeat". The bug this module exists to fix was
not a small pool, it was a pool with no memory: the skill file told a model to
"pick one at random", and a model has no record of yesterday's briefing. So
the tests that matter assert the ledger actually suppresses a recent quote,
and they are written so that deleting the ledger turns them red.

The second group is the filter. The live endpoint really does return stage
dialogue, Latin with a parenthetical translation, doubled words and fragments
carrying an orphan quote mark; each fixture below is a row that was actually
observed, not an invented shape.
"""
import json

import pytest

import quotes

# Captured at import, before the autouse fixture below replaces it. The two
# fetch tests exercise the real function and restore it explicitly.
_REAL_FETCH = quotes.fetch


@pytest.fixture(autouse=True)
def _state(tmp_path, monkeypatch):
    """Point state at a temp dir and cut the network by default.

    Every test that wants the network says so explicitly. A suite that reaches
    a third-party endpoint is a suite that fails when someone else's host does.
    """
    monkeypatch.setenv("VAN_GOGH_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(quotes, "fetch", lambda bulk=True: [])
    return tmp_path


def _pool(n):
    """n real quotes off the curated list.

    Synthetic text cannot be used any more: `pick` treats curation as a
    membership test and drops anything not on the list, which is what stops a
    stale cache from diluting the pool.
    """
    assert n <= len(quotes.CURATED), "asked for more quotes than exist"
    return [dict(q) for q in quotes.CURATED[:n]]


# -- No repeat: the actual complaint ----------------------------------------

def test_a_quote_shown_today_cannot_return_tomorrow(_state):
    """The one-line statement of the bug."""
    quotes.save_state({"cache": _pool(10), "recent": []})
    first, commit = quotes.pick(allow_network=False)
    commit()
    second, _ = quotes.pick(allow_network=False)
    assert second["text"] != first["text"]


def test_no_repeat_across_a_full_cache_cycle(_state):
    """With a cache larger than the ledger, nothing repeats inside 30 runs.

    Deleting the recency filter in `pick` makes this fail: random choice over
    a 40-quote pool collides well inside 30 draws (birthday bound).
    """
    quotes.save_state({"cache": _pool(40), "recent": []})
    seen = []
    for _ in range(30):
        q, commit = quotes.pick(allow_network=False)
        commit()
        seen.append(q["text"])
    assert len(set(seen)) == 30, "a quote repeated inside the memory window"


def test_ledger_is_capped_and_holds_the_most_recent(_state):
    quotes.save_state({"cache": _pool(60), "recent": []})
    last = None
    for _ in range(RUNS := quotes.RECENT_MEMORY + 15):
        q, commit = quotes.pick(allow_network=False)
        commit()
        last = q["text"]
    state = quotes.load_state()
    assert len(state["recent"]) == quotes.RECENT_MEMORY
    assert state["recent"][-1] == last


def test_pick_does_not_record_until_commit(_state):
    """Selection is not evidence the reader saw it.

    Mirrors travel.py's deferred notice ledger. A run that dies before it
    prints must not have burned the quote.
    """
    quotes.save_state({"cache": _pool(10), "recent": []})
    quotes.pick(allow_network=False)          # commit deliberately not called
    assert quotes.load_state()["recent"] == []


def test_exhausted_pool_still_returns_a_quote(_state):
    """More briefings than quotes must degrade, never crash or return None."""
    quotes.save_state({"cache": _pool(3), "recent": []})
    for _ in range(20):
        q, commit = quotes.pick(allow_network=False)
        commit()
        assert q["text"] and q["author"]


def test_pool_rotates_without_immediate_repeats(_state):
    """A fresh install draws a full memory window without a repeat.

    The guarantee is the ledger depth, not the pool size: after 30 draws the
    oldest quote legitimately becomes eligible again.
    """
    seen = []
    for _ in range(quotes.RECENT_MEMORY):
        q, commit = quotes.pick(allow_network=False)
        commit()
        seen.append(q["text"])
    assert len(set(seen)) == quotes.RECENT_MEMORY


# -- The filter: every fixture is a row the live endpoint returned -----------

@pytest.mark.parametrize("label,row", [
    ("stage dialogue", {"text": "Theseus: What is the crime for which you must pay by death? Phaedra: My life.",
                        "author": "Seneca"}),
    ("latin with translation", {"text": "Istam terra de fossam premat (As for her, let her be buried deep in the earth, and heavy may the soil lie).",
                                "author": "Seneca"}),
    ("doubled word", {"text": "Even chance is not divorced from nature, from the inweaving and and enfolding of things.",
                      "author": "Marcus Aurelius"}),
    ("orphan quote mark", {"text": "A poor soul burdened with a corpse,' Epictetus calls you.",
                           "author": "Marcus Aurelius"}),
    ("blank author", {"text": "There is no cure for birth and death save to enjoy the interval.",
                      "author": ""}),
    ("handle as author", {"text": "Do not care what others think of you, be an unapologetic rebel and do your own thing.",
                          "author": "TheAncientSage@"}),
    ("paragraph length", {"text": "x" * (quotes.MAX_LEN + 1), "author": "Seneca"}),
    ("too short", {"text": "Be one.", "author": "Marcus Aurelius"}),
    ("not a dict", "just a string"),
    # Found by cold-reading a 79-quote sample sorted by AUTHOR. An earlier read
    # of the same pool sorted longest-first reported it clean, because every
    # one of these is short and sat at the bottom where that ordering buried
    # them. One ordering is not a cold read.
    ("trailing footnote marker", {"text": "Our own worth is measured by what we devote our energy to.  4.",
                                  "author": "Marcus Aurelius"}),
    ("appended chapter title", {"text": "Sometimes even to live is an act of courage.\u201d   Anger.",
                                "author": "Seneca"}),
    ("orphan curly quote", {"text": "A little wisp of soul carrying a corpse.\u201d, Epictetus.",
                            "author": "Marcus Aurelius"}),
    ("self harm", {"text": "Can you no longer see a road to freedom? It's right in front of you. You need only turn over your wrists.",
                   "author": "Seneca"}),
    ("self harm, veins", {"text": "When the time comes there is no shame in opening your veins and departing.",
                          "author": "Seneca"}),
    ("all caps heading", {"text": "HE WHO ACTS UNJUSTLY ACTS IMPIOUSLY.",
                          "author": "Marcus Aurelius"}),
])
def test_unusable_rows_are_rejected(label, row):
    """Each asserted on its own: a set-level scan passes while anything matches."""
    assert quotes.is_usable(row) is False, f"{label} should have been rejected"


@pytest.mark.parametrize("text", [
    "Not to be offended with other men's liberty of speech, and to apply myself unto philosophy.",
    "Choose not to be harmed, and you won’t feel harmed. Don’t feel harmed, and you haven’t been.",
    "Nothing that goes on in anyone else’s mind can harm you, nor the shifts and changes around you.",
])
def test_apostrophes_inside_words_are_not_orphan_quote_marks(text):
    """The orphan-quote filter must not eat ordinary contractions.

    Counting bare `'` would reject "men's" and "won't", which is most of the
    corpus. This is the over-rejection case the filter is narrowed against.
    """
    assert quotes.is_usable({"text": text, "author": "Marcus Aurelius"}) is True


@pytest.mark.parametrize("text", [
    # Death is ordinary Stoic subject matter and most of the corpus touches
    # it. The self-harm filter names an ACT, so these must all survive; a
    # filter keyed on "death" or "die" would empty the pool.
    "He who fears death will never do anything worthy of a man who is alive.",
    "In the ashes all men are levelled. We're born unequal, we die equal.",
    "Everyone goes out of life just as if he had but lately entered.",
    "It is not that we have a short time to live but that we waste a lot of it.",
    "Do every act of your life as if it were your last.",
    # Ends on a capitalised word after a full stop, but it is a real sentence,
    # not an appended chapter title.
    "Today I escaped from anxiety. Or no, I discarded it, because it was within me, not outside.",
])
def test_quotes_about_death_are_not_mistaken_for_self_harm(text):
    assert quotes.is_usable({"text": text, "author": "Seneca"}) is True


def test_a_sentence_starting_with_a_capitalised_word_is_kept():
    """The all-caps filter measures the whole line, not its first word.

    Scanned editions open sentences with a capitalised first word ("BEGIN the
    morning by saying...") and that is ordinary text, not a heading.
    """
    assert quotes.is_usable({
        "text": "BEGIN the morning by saying to yourself that you will meet the ungrateful.",
        "author": "Marcus Aurelius"}) is True


def test_spaced_dash_does_not_leave_doubled_spaces():
    """The strip must clean up the whitespace it creates.

    A spaced em-dash ("law <em> have") turns into ", " beside the space that
    was already there, leaving "law ,  have". This shipped into the live pool.
    """
    em = chr(0x2014)
    got = quotes.normalize({
        "text": f"It is a universal law {em} have no illusion {em} that every creature is attached to self.",
        "author": "Epictetus"})["text"]
    assert "  " not in got, f"doubled space survived: {got!r}"
    assert " ," not in got, f"space before comma survived: {got!r}"
    assert got.startswith("It is a universal law, have no illusion, that")


def test_every_curated_quote_passes_the_filter():
    """A bad hand-edit to the list must not be able to ship."""
    for q in quotes.CURATED:
        assert quotes.is_usable(q), f"curated quote rejected: {q['text']!r}"


def test_curated_pool_is_deep_enough_for_the_ledger():
    """The pool must exceed the memory window, or repeats become unavoidable."""
    assert len(quotes.CURATED) > quotes.RECENT_MEMORY * 2


def test_curated_quotes_are_unique():
    texts = [q["text"] for q in quotes.CURATED]
    assert len(set(texts)) == len(texts)


def test_no_near_duplicate_translations_in_the_pool():
    """Two translations of one line read as a repeat even though the text differs."""
    import difflib
    texts = [q["text"].lower() for q in quotes.CURATED]
    for i, a in enumerate(texts):
        for b in texts[i + 1:]:
            r = difflib.SequenceMatcher(None, a, b).ratio()
            assert r <= 0.75, f"near-duplicate ({r:.2f}): {a[:50]!r} / {b[:50]!r}"


def test_a_stale_cache_cannot_reintroduce_an_uncurated_quote(_state):
    """The leak this fix closed: 17 of 19 cached quotes bypassed curation.

    A cache written by an earlier version holds whatever the API served then.
    Curation is a membership test, so those must be dropped on read rather
    than quietly diluting the pool.
    """
    quotes.save_state({"cache": [
        {"text": "Stupidity is expecting figs in winter, or children in old age.",
         "author": "Marcus Aurelius"},
        {"text": "Whatever can happen at any time can happen today.",
         "author": "Seneca"},
    ], "recent": []})
    approved = {q["text"] for q in quotes.CURATED}
    for _ in range(15):
        q, commit = quotes.pick(allow_network=False)
        commit()
        assert q["text"] in approved, f"uncurated quote surfaced: {q['text']!r}"


def test_the_figs_quote_is_not_in_the_pool():
    """The specific quote that prompted the curation pass."""
    assert not any("figs in winter" in q["text"] for q in quotes.CURATED)


# -- Dashes: the strip is the guarantee, not the prompt ----------------------

def test_dashes_are_stripped_on_the_way_into_the_cache():
    em, en = chr(0x2014), chr(0x2013)
    got = quotes.normalize({"text": f"Waste no more time{em}be one{en}today.",
                            "author": f"Marcus{em}Aurelius"})
    assert em not in got["text"] and en not in got["text"]
    assert em not in got["author"] and en not in got["author"]


def test_no_curated_quote_carries_a_dash():
    em, en = chr(0x2014), chr(0x2013)
    for q in quotes.CURATED:
        assert em not in q["text"] and en not in q["text"]
        assert em not in q["author"] and en not in q["author"]


def test_rendered_quote_carries_no_dash_and_matches_the_shipped_shape(_state):
    quotes.save_state({"cache": _pool(10), "recent": []})
    q, _ = quotes.pick(allow_network=False)
    line = quotes.render(q)
    assert chr(0x2014) not in line and chr(0x2013) not in line
    assert line.startswith('> "'), "briefing prints the quote as a blockquote"
    assert line.endswith(q["author"])


# -- Failing open: a quote is never worth a failed briefing ------------------

def test_corrupt_state_file_still_yields_a_quote(_state):
    (_state / "quotes.json").write_text("{ not json at all", encoding="utf-8")
    q, commit = quotes.pick(allow_network=False)
    commit()
    assert q["text"]


def test_state_file_of_the_wrong_shape_is_survived(_state):
    (_state / "quotes.json").write_text(json.dumps(["a", "list", "not", "a", "dict"]),
                                        encoding="utf-8")
    q, _ = quotes.pick(allow_network=False)
    assert q["text"]


def test_unwritable_state_dir_does_not_raise(_state, monkeypatch):
    """save_state swallows: the briefing outranks the ledger."""
    monkeypatch.setenv("VAN_GOGH_STATE_DIR", "/proc/nonexistent/nope")
    q, commit = quotes.pick(allow_network=False)
    commit()
    assert q["text"]


def test_network_failure_falls_back_to_cache(_state, monkeypatch):
    def boom(bulk=True):
        raise RuntimeError("endpoint down")
    monkeypatch.setattr(quotes, "fetch", boom)
    quotes.save_state({"cache": _pool(10), "recent": []})
    with pytest.raises(RuntimeError):
        quotes.fetch()          # the fixture really does raise
    # ...but pick never calls it when the unseen pool is deep enough.
    q, _ = quotes.pick(allow_network=True)
    assert q["text"]


def test_fetch_returns_empty_list_on_a_bad_response(monkeypatch):
    """Every fetch error path is [], so the cache carries the day."""
    # The autouse fixture stubs `fetch` itself; this test is about the real one.
    monkeypatch.setattr(quotes, "fetch", _REAL_FETCH)
    class _Resp:
        def raise_for_status(self): raise RuntimeError("500")
        def json(self): return {}
    monkeypatch.setattr(quotes.requests, "get", lambda *a, **k: _Resp())
    assert quotes.fetch() == []


def test_fetch_filters_bad_rows_out_of_a_good_response(monkeypatch):
    """A bad row is rejected once at fetch, never cached to be picked later."""
    monkeypatch.setattr(quotes, "fetch", _REAL_FETCH)
    payload = [
        {"text": "Make the best use of what is in your power, and take the rest as it happens.",
         "author": "Epictetus"},
        {"text": "Theseus: What is the crime? Phaedra: My life.", "author": "Seneca"},
    ]
    class _Resp:
        def raise_for_status(self): pass
        def json(self): return payload
    monkeypatch.setattr(quotes.requests, "get", lambda *a, **k: _Resp())
    got = quotes.fetch()
    assert len(got) == 1 and got[0]["author"] == "Epictetus"


def test_cache_is_capped(_state, monkeypatch):
    """Without a cap the state file grows for the life of the install."""
    monkeypatch.setattr(quotes, "MAX_CACHE", 10)
    monkeypatch.setattr(quotes, "fetch", lambda bulk=True: _pool(len(quotes.CURATED)))
    quotes.save_state({"cache": [], "recent": []})
    _, commit = quotes.pick(allow_network=True)
    commit()
    assert len(quotes.load_state()["cache"]) <= 10
