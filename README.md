# Hanzi Miner

Paste Chinese text → get **i+1 flashcards**: sentences where you understand every
word except exactly one. That single unknown word is guessable from context,
which is the condition under which vocabulary actually sticks (Krashen's
comprehensible input).

Paste Chinese, mine the sentences worth learning, review them on a schedule, and
generate fresh dialogues built from the words you keep forgetting.

**Mine** → **Review** → **Library** → **Generate**. No app to install, no account.

```
paste dialogue →  clean + segment → keep units with exactly 1 unknown word
              → save to the word bank → review on an SRS schedule
              → generate a new dialogue around your weakest words → repeat
```

The Anki `.apkg` export is still there, but it is no longer the only way out —
the deck lives in the app now.

## Quick start

```bash
git clone <this repo> && cd hanzi-miner
./scripts/fetch_data.sh
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

createdb hanzi
export DATABASE_URL=postgresql://localhost/hanzi
export DO_INFERENCE_KEY=doo_v1_...

python app.py
```

`fetch_data.sh` downloads CC-CEDICT and the HSK lists once. `createdb`,
`DATABASE_URL` and `DO_INFERENCE_KEY` are only needed for the save/review/generate
half; `python app.py` serves on http://127.0.0.1:8080.

Without `DATABASE_URL` you still get mining and Anki export; the other tabs say so
instead of failing silently. Without `DO_INFERENCE_KEY` only the Generate tab is off.

Or use the miner from the terminal:

```bash
python cli.py samples/dialogue_office.txt --level 3 --mode dialogue
cat article.txt | python cli.py - --level 4 --mode prose --max-unknown 1
```

## What it does, in detail

1. **Clean.** Strips subtitle timestamps (`00:01:23,000`), speaker labels
   (`小明：`, `A:`, `Speaker 1:`), HTML entities and pure-English lines.
2. **Segment.** Chinese has no spaces, so word boundaries come from [jieba].
3. **Filter to i+1.** Keep only units containing *exactly one* word outside your
   known-set. `max_unknown=2` relaxes this when you're just starting.
4. **Mine dialogue by exchange, not by line.** Short lines (`好。`) carry no
   context, so the miner also evaluates 2-line windows and keeps whichever window
   has exactly one unknown — that's what makes the word guessable.
5. **Dedupe.** One card per target word, preferring the shortest, cleanest unit.
6. **Rank.** Genuinely-new words first, then the words this particular text leans
   on (so the deck matches what you're actually reading), then real dictionary
   words, then shorter cards.
7. **Enrich + export.** CC-CEDICT definitions, pypinyin tone-marked readings, and
   a ready-to-import Anki `.apkg` (no add-ons needed). Re-imports update the same
   deck instead of duplicating cards.

## Review, library and generation

**Save** puts a mining result in the database: the source text, one row per word,
and the sentence each word appeared in. Words are upserted — mining a word you
already have bumps a counter, it never duplicates the row or disturbs its schedule.

**Review** runs a deliberately small SRS. Every word has a box (0–6) and a due
date. *Good* promotes the box and pushes the date out (1, 2, 4, 8, 16, 32 days);
*Again* resets to box 0, so a lapsed word comes straight back; *Easy* jumps two
boxes. The front of the card shows the sentence with the target word masked, so you
recall it from context rather than recognising it in isolation.

**Library** is the word bank (filter by all / due / new / learning / known, search
by word, pinyin or meaning, each row showing its context sentence) plus every text
you've saved or generated, expandable in place.

**Generate** writes a brand-new dialogue around your weakest and most overdue words.
It picks them from the bank, asks the model for a dialogue that uses all of them at
HSK level N, mines the result, and files it in the library — so the new words land in
your review queue in one click.

### Generation needed two defences (learned the hard way)

The first version shipped garbage. `qwen3.8-max` is a *reasoning* model, and with
only a "no commentary" instruction it wrote pages of English self-debate about HSK
levels, interleaved with Chinese drafts. Every line containing a Chinese character
survived naive parsing, so the app stored the model's monologue as a dialogue — and
the tests passed, because the target words really were in there somewhere.

Two fixes, both in `generate.py`:
1. Ask for the output wrapped in `<dialogue>` tags and parse only inside them.
2. Accept a line **only** when its speaker label is exactly one of the two names
   requested **and** the turn contains no Latin letters or ASCII digits. Chinese
   dialogue has neither, and this kills every reasoning line.

`scripts/model_probe.py` re-runs the comparison across candidate models. Run it
before changing the default: the failure mode is not an error, it is a model that
quietly emits its chain of thought.

## The part that actually matters: your known-set

Everything depends on knowing what you already know. Get this wrong and you get
junk cards. The miner assembles the known-set from three sources:

| Source | What it is | Why |
|---|---|---|
| HSK 1–6 (`data/hsk_L*.txt`) | ~5000 words, pedagogically ordered | sets your baseline level |
| Frequency list (`data/freq_zh.txt`) | top 5000 Chinese words | **fills gaps in the HSK export** |
| `--extra-known` / the textarea | your own list (Anki export, etc.) | always the best signal |

The frequency list is not optional in practice: the HSK 2.0 export omits common
function words (`没`, rank 82) and greetings (`你好`), which otherwise show up as
false "unknown" words on every card. Merging frequency + HSK fixes that.

**Complex forms.** `你好`, `有意思`, `看书` are single jieba tokens whose
characters you already know, so a word-list check calls them "unknown". They are
demoted (flagged `complex form`) rather than dropped — dropping them would also
silently kill real targets like `语法` (`语` + `法` are both known words).

**If results look wrong**, the known-set is almost always why. Fixes, in order:
paste your Anki export into the extra-known box → lower/raise the level → try
`--max-unknown 2`.

## CLI reference

```
python cli.py PATH --level {1..6} --mode {dialogue,prose}
                   --max-unknown N --window N --extra-known "词1,词2"
                   --limit N --no-pinyin
```

| Flag | Default | Meaning |
|---|---|---|
| `--level` | 2 | HSK 1–N plus top-N×500 frequent words = known |
| `--mode` | dialogue | `dialogue` mines across turns; `prose` per sentence |
| `--max-unknown` | 1 | how many new words a card may contain |
| `--window` | 2 | dialogue turns merged per unit |
| `--extra-known` | — | words to add to the known-set |

## Data / offline use

`scripts/fetch_data.sh` downloads CC-CEDICT (CC BY-SA 4.0) and the HSK lists.
`data/freq_zh.txt` was generated once with the [`wordfreq`][wordfreq] package
(`top_n_list('zh', 20000)`, filtered to pure-CJK tokens) and committed, so
`wordfreq` is *not* a runtime dependency — the app ships the list.

`data/cedict.txt` is ~10 MB and is gitignored; the App Platform build must run
`scripts/fetch_data.sh` (or commit the file) before first boot.

## Deploy

`.do/app.yaml` is the App Platform **creation template**: one 0.5 GB instance in
`tor` (~$5/mo) plus a dev PostgreSQL (512 MiB, $7/mo — no backups, no HA). Source is
a public `git:` clone URL, which is what avoids needing a browser OAuth grant; the
cost is that `git push` does not redeploy.

```bash
doctl apps create --spec .do/app.yaml
doctl apps spec get <app-id> > .do/app.live.yaml
```

The second command matters: **after the first deploy, never reuse this template
for an update** — it would wipe the secret (see below). Branch future edits from
`.do/app.live.yaml`.

Two gotchas that both look like success:

- **A spec update reuses the previous build.** A generic `git:` source won't pick up
  new commits from `apps update --spec`; the build reports `PreviousBuildReused` and
  the app keeps running the old commit under a green `ACTIVE`/`HEALTHY`. Check
  `source_commit_hash` against your HEAD and force a real one with
  `POST /v2/apps/<id>/deployments {"force_build": true}`.
- **A `SECRET` env var cannot live in a git-tracked file.** App Platform encrypts
  the plaintext on first submit and returns an opaque `EV[...]` blob thereafter, so
  re-submitting the template *deletes* the key. Always branch from the live spec.

## Credentials

The app needs exactly one secret: `DO_INFERENCE_KEY`, a DigitalOcean **Model Access
Key** for dialogue generation. `DATABASE_URL` is bound to the app's database and
managed by App Platform.

**DigitalOcean retired programmatic creation of model access keys** — the API
endpoint returns `410 Gone` ("Go to manage page in the control panel"), and the
alternate path 404s. Minting is console-only:

1. https://cloud.digitalocean.com/model-studio/manage-keys → create `hanzi-miner-app`
2. Scope it to **only the models the app calls** (`qwen3.8-max`), not "All models"
3. Hand it to the app without recording it anywhere:

```bash
read -rs DO_INFERENCE_KEY && export DO_INFERENCE_KEY
python scripts/set_app_key.py <app-id>
unset DO_INFERENCE_KEY
```

`read -rs` hides the input and keeps the value out of `~/.zsh_history` (a plain
`export KEY=value` would record it).

`set_app_key.py` fingerprints the key, **tests it against the inference endpoint
before repointing the app** (so a bad key can't take down a working deployment),
then edits the live spec. `scripts/rotate_app_key.py` is the same thing with the
create step attempted first, and it fails with a clear message on the retired
endpoint.

Use a key dedicated to this app rather than a shared one: you can revoke
`hanzi-miner-app` in the console without touching anything else that uses inference.

## Roadmap

- [ ] Photo input — iOS Shortcut (on-device Live Text) → same `/api/mine`
- [ ] `.srt` ingestion with subtitle-line reassembly
- [ ] Sentence audio via TTS (DigitalOcean serverless inference) stored in Spaces
- [ ] Export the word bank back to Anki on demand (currently only per-text export)

## Credits

- [CC-CEDICT][cedict] dictionary (CC BY-SA 4.0)
- HSK 2.0 word lists via [hskhsk.com][hsk]
- [jieba] for Chinese segmentation, [genanki] for Anki export, [pypinyin] for readings

[cedict]: https://www.mdbg.net/chinese/dictionary?page=cc-cedict
[hsk]: https://github.com/glxxyz/hskhsk.com
[jieba]: https://github.com/fxsjy/jieba
[genanki]: https://github.com/kerrickstaley/genanki
[pypinyin]: https://github.com/mozillazg/python-pinyin
[wordfreq]: https://github.com/rspeer/wordfreq
