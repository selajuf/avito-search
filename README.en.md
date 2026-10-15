# Avito Search

**Find clothes on Avito using your own criteria. Save good finds and skip listings you have already rejected.**

[Русский](README.md) · [Agent installation guide](INSTALL.md) · [Browser setup](skills/avito-search/references/browser.md)

A skill for Codex, OpenCode and Antigravity. Your agent browses listings in a
visible browser, checks their photographs and builds a local HTML gallery.
Future searches take your saved criteria and feedback into account.

## What it does

- Searches from a description or reference photographs you provide.
- Switches from recommendations to targeted search when the feed has nothing suitable.
- Compares photographs, price, size, material, condition and availability.
- Assigns permanent listing numbers and saves your feedback.
- Builds a page with large photographs, listing details and links to Avito.

Your agent performs the search. This is not a standalone bot or an official
Avito API. The browser, database and galleries run on your computer; your
chosen model runs under the terms and settings of your agent environment.

## The easiest way

Paste this into your agent:

> Install the Avito Search skill from https://github.com/selajuf/avito-search into this project. Read INSTALL.md, check the dependencies and prepare the visible browser. Do not change global settings or import cookies from other profiles. Then ask me what clothes I want to find.

The package starts with an empty profile. It contains none of the author's
listings, sizes, preferences or keys, and has no AI Wiki connection. The skill
does not need a separate model API key; you need a working agent with access
to your chosen model.

## Manual installation

You need Python 3.10+, Git, Bun and a graphical desktop. A current stable Bun
release is recommended. See [Browser setup](skills/avito-search/references/browser.md)
for the exact requirements and pinned gstack version.

```sh
git clone https://github.com/selajuf/avito-search.git
cd avito-search
python3 install.py --project ../my-avito
cd ../my-avito
python3 .agents/skills/avito-search/scripts/setup_browser.py
```

Open `my-avito` in your agent and say:

> Use avito-search. I want to find clothes based on these photographs. First ask which criteria matter, then put together a selection.

The installer copies the complete skill, including its helpers, into
`.agents/skills/avito-search`. The separate browser setup downloads the pinned
gstack/browse source and Chromium. It does not change your global gstack
installation, hooks or agent permissions.
For Claude Code, use `python3 install.py --project ../my-avito --host claude`.

## Your local data

By default, everything is stored in the working project's `.avito-search/`:

```text
.avito-search/
├── preferences.json       your search criteria
├── data/catalog.sqlite    listings and decisions
├── media/                 photographs and references
├── galleries/             HTML galleries
├── browser/               isolated profile and browser state
└── runtime/               browser code and Chromium
```

Set `AVITO_HOME` to use a persistent location elsewhere. Do not commit this
directory to Git. A gallery may contain your notes and photographs; it is
not published automatically.

Here, “learning” means saving your profile and decisions, not training a new
model. You can change your criteria and start new selections.

## Limitations

When a CAPTCHA appears, the agent stops so you can complete it in the visible
tab. The skill does not promise to bypass protection or keep Avito accessible.
The site's DOM may change. A photograph alone cannot confirm fit,
authenticity or whether the seller still has an item.

The skill layout is compatible with current Codex, OpenCode and Antigravity
skill formats. See [Release verification](docs/VERIFICATION.md) for the
environments actually checked. Windows is currently considered through WSL2
with a graphical environment; native Windows support is not claimed.

Unless you explicitly ask, the agent does not message sellers, place orders,
change VPN settings or import existing sessions.

## Development

```sh
python3 -m unittest discover -s tests -v
```

Tests use temporary directories and fictional data. The working selection
format is documented in [references/catalog.md](skills/avito-search/references/catalog.md).

MIT · [gstack and dependencies](THIRD_PARTY_NOTICES.md).
