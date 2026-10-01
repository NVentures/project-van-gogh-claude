"""
oauth_scopes.py: the one place Van Gogh names the permissions it asks for.

The rule this file exists to hold:

    Scopes are named at the CONSENT screen and nowhere else.

A refresh token carries the scopes its owner approved on the day they signed
in, and that set never grows by itself. Both providers reject a refresh that
asks for a scope the token was never granted, and they reject the WHOLE
refresh, not just the new part. So the day a release added two scopes here and
the Google client sent this list on every refresh, every account connected
before that release lost Gmail and Calendar together, over a permission only
one optional feature wanted.

What follows from the rule:

  * Google refreshes name no scopes at all (google_client._credentials passes
    scopes=None). Google then answers with a token carrying whatever the user
    actually granted.
  * Microsoft refreshes must name scopes (MSAL requires a list), so they name
    the scopes THAT ACCOUNT was granted, stored beside its token at sign-in,
    or MS_BASELINE_SCOPES for an account that signed in before the list was
    stored. Never MS_SCOPES. See microsoft_client.refresh_scopes.
  * Adding a scope to GOOGLE_SCOPES or MS_SCOPES is therefore safe for every
    existing account. The feature that needs the new scope owns the other
    half: an older token answers its call with a 403, and the feature turns
    that into a plain "sign in again" sentence for that one account
    (contact_capture.SCOPE_HELP and send_email._is_missing_send_scope are the
    two worked examples). Nothing else may stop working.

tests/test_oauth_scope_rule.py enforces all three.
"""

GOOGLE_SCOPES = [
    # gmail.modify covers reading, drafting and labelling but NOT sending:
    # drafts.send and messages.send both require gmail.send (or compose).
    # Without it the page's stamp fails with a 403 the moment it is pressed.
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/drive",
    # Contact capture (app/contact_capture.py) needs both: the .other.readonly
    # scope to READ the "Other contacts" bucket Gmail auto-collects, and the
    # full contacts scope to WRITE the promoted contact. copyOtherContactToMy-
    # ContactsGroup requires both at once, and "Other contacts" is read-only,
    # so a promotion is always a copy followed by an update.
    "https://www.googleapis.com/auth/contacts",
    "https://www.googleapis.com/auth/contacts.other.readonly",
]

_GRAPH = "https://graph.microsoft.com/"

# What the Microsoft consent screen asks for. This list may grow.
MS_SCOPES = [
    _GRAPH + "Mail.ReadWrite",
    _GRAPH + "Mail.Send",
    _GRAPH + "Calendars.ReadWrite",
    _GRAPH + "User.Read",
]

# What an account that signed in BEFORE granted scopes were stored is refreshed
# with. FROZEN: it records what those accounts were granted, which is history
# and cannot change. A new scope goes in MS_SCOPES above and never here.
MS_BASELINE_SCOPES = (
    _GRAPH + "Mail.ReadWrite",
    _GRAPH + "Mail.Send",
    _GRAPH + "Calendars.ReadWrite",
    _GRAPH + "User.Read",
)

# MSAL adds these itself and raises if a caller passes them.
MS_RESERVED_SCOPES = frozenset({"openid", "profile", "offline_access", "email"})
