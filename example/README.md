# Example folder

This folder contains **fictitious, placeholder** content to illustrate the data format
used by the agent. Nothing here is real: the email addresses, names, appointments and
config values are all fabricated examples. Never put real personal data in this folder
(or anywhere in the repo).

## Contents

- `emails/` — sample `mail.json` files as produced by `poll_gmail.py` for emails waiting
  to be categorized (`da_smistare`) and already-categorized emails (`smistate/<category>`).
- `config.example.json` — template for the gitignored `maps_config.json` (also copied at the repo root).
- `schedule.example.txt` — example format of the extracted-appointments file (`schedule.txt`).

## `mail.json` format

Each saved email produces a `mail.json` like the examples below. The `id` is a Gmail
message id, `headers` include From/To/Cc/Subject/Date, `body_text` is the decoded plain
text, `attachments` lists any saved files, and `categoria` (when present) stores the
assigned category.

See `emails/raw_sample.json`, `emails/categorized_sample.json` and the `emails/smistate/`
folder for the exact structure.
