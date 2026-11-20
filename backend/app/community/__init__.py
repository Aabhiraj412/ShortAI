"""Stage 3.32E: YouTube Community post DRAFT automation.

Generates, validates and manages local Community post drafts (text plus one image). Nothing in this package can
publish: it imports no YouTube client, makes no YouTube API call and has no upload path. "Approved" means the
operator approved the draft text and image; posting stays a manual action outside the system until Stage 3.32F.

Modules: ``store`` (``community_*`` tables), ``content`` (sources, topic and format selection, text), ``images``
(licensed-asset cards and typographic cards), ``validation`` (text and image checks), ``service`` (lifecycle,
schedule, limits, notifications).
"""
