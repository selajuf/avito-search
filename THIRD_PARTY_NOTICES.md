# Third-party software

The browser runtime is [gstack/browse](https://github.com/garrytan/gstack),
Copyright (c) 2026 Garry Tan, licensed under the MIT License.

The setup helper fetches commit
`47d639abffa882b8cbb641d7d1dedf08ecada9ec` into the user's local runtime
directory. It does not distribute an existing browser profile, cookies, tokens,
or the author's local gstack installation. The upstream license remains in
the downloaded checkout. The runtime changes needed for an isolated CLI-only
browser are documented in the skill's `references/browser.md`.

Chromium is downloaded through Playwright. Chromium, Playwright, Bun and
gstack's dependencies retain their respective upstream licenses.

Avito Search is an independent project and is not affiliated with Avito,
OpenAI, OpenCode, Google or gstack.

