# Avito Search

This repository distributes the self-contained skill in `skills/avito-search/`.
For installation, read `INSTALL.md`. For shopping, load that skill's `SKILL.md`.
Do not assume that a browser, an Avito login, or user preferences already exist.

`install.py` copies the skill into a chosen project. `setup_browser.py` installs
the pinned browser dependency. They do not modify agent permissions, global
configuration, or an existing browser profile.

User data belongs under `.avito-search/` in the working project, or an explicit
`AVITO_HOME`. Never commit that directory, cookies, runtime state, local keys,
personal references, a catalog, or an exported user gallery. Use synthetic
fixtures and temporary directories for development tests.

