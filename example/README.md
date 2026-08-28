# Example folder

This folder contains **fictitious, placeholder** content to illustrate the data format
used by the agent. Nothing here is real: the email addresses, names, appointments and
config values are all fabricated examples. Never put real personal data in this folder
(or anywhere in the repo).

## Contents

- `emails/to_sort/` — sample `mail.json` files as produced by `poll_gmail.py` for emails
  **waiting to be categorized** (the agent's runtime equivalent is `Inbox/to_sort/`).
- `emails/sorted/` — sample already-categorized emails (each folder holds `mail.json` +
  `categoria.txt`), mirroring the runtime `Inbox/sorted/<category>/` layout.
- `config.example.json` — template for the gitignored `maps_config.json` (also copied at the repo root).
- `schedule.example.txt` — example format of the extracted-appointments file (`schedule.txt`).

## `mail.json` format

Each saved email produces a `mail.json` like the examples in this folder. The `id` is a
Gmail message id, `headers` include From/To/Cc/Subject/Date, `body_text` is the decoded
plain text, `attachments` lists any saved files, and `categoria` (when present) stores
the assigned category. When the agent saves a new email that has not been categorized
yet, no `categoria` field is present and the email lives under `to_sort/`.

## Note on `Inbox/`

The `Inbox/` folder at the repo root is **not** committed (see `.gitignore`). It is the
runtime folder the agent creates and fills with your real emails at run time. The
fictitious examples you are meant to inspect live right here in `example/emails/`.
