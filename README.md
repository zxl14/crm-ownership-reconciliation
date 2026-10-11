# Bellhaven facility review

Read-only reconciliation of the Bellhaven website against the CRM sandbox, with a Streamlit review screen. Finding decisions stay on this computer. An executable CRM approval is a second, separate record. CRM writes stay off unless `CRM_WRITES_ENABLED` is exactly `true`, and this project does not set that variable.

The completed CRM corrections are the Owosso duplicate PATCH and the Findlay parent PATCH. Owosso `001QU150PM4Z15UA71` is Inactive and its `duplicate_of_account` is `001EGU7BMJ942ZTRE6`. Findlay `001UKEFGADQ8YCZ4YM` now has parent `0015QAPLGS3FVYEEEM`, remains Active, and still has lifetime revenue 22000 and outstanding receivables 0. A fresh read-only reconciliation does not propose either change again.

## Architecture

- `src/scrape_website.py` reads the public directory, detail pages, homepage, and About page.
- `src/crm_refresh.py` downloads accounts with GET `/api/v1/accounts` and replaces the local file.
- `src/reconcile.py` matches facilities to CRM accounts.
- `src/findings.py` turns those matches into stable review findings.
- `src/ownership.py` holds the Findlay proposal and the acquisition summary.
- `src/review_store.py` saves finding decisions. A decision cannot authorize an API write.
- `src/writeback.py` saves an executable approval. The verified duplicate body and the one Findlay parent body can be sent only when writes are explicitly enabled and that exact approval matches the current account. The review screen does not turn writes on.
- `app.py` is the review screen. On open it refreshes the public site text and the CRM account file, then rebuilds findings.

## Matching

A confident match requires the same normalized street, city, state, and ZIP. Normalization lowercases text, removes punctuation, and expands whole words such as St/Street, Rd/Road, and W/West. The same normalized name supports a match and is not enough by itself. A Bellhaven page does not, by itself, mean the CRM parent should change.

Low-confidence rows share a normalized name, city, state, and ZIP while the street differs. They stay questions. Parent accounts, identified by a name ending in `(Parent Account)`, are excluded from facility matching.

## Financial safeguards

Before any parent change, read `lifetime_revenue` and `outstanding_ar`.

- If both are greater than zero, preserve the old account and its parent, create a new facility account under the verified new parent, and set `chow_current_account` on the old account to the new account id. Historical revenue and receivables are not copied into the request.
- Otherwise, a justified parent change is a direct `parent_id` update.
- Findlay is the direct-update case: lifetime revenue is 22000 and outstanding receivables are 0. Marietta and Tiffin are financial-hold cases. They are not confirmed parent changes. If a parent change were later verified, they would use the preserve-and-create rule.
- The local tests in `src/check_phase4.py` cover both branches, a failed second step, and a rerun that does not create a second account. Those tests use a fake session.

## Findlay ownership proposal

The homepage says Bellhaven Meadows of Findlay joined the Bellhaven family. That sentence was the website evidence. The matched account is `001UKEFGADQ8YCZ4YM` at `1800 N Blanchard St, Findlay, OH 45840`. Its parent is now `0015QAPLGS3FVYEEEM`, Bellhaven Senior Living (Parent Account). Lifetime revenue remains 22000 and outstanding receivables remain 0.

The parent id was an inference: the homepage does not print it. The applied body was exactly `{"parent_id": "0015QAPLGS3FVYEEEM"}`. Because the account already has that parent, a later reconciliation treats it as a confirmed Bellhaven match and does not queue another parent update. The write-back still refuses a second request when the parent, revenue, receivables, and status already match.

## Acquisition context

The About page welcomes the Harborview Care Group family of communities in 2025 and says select communities joined from Cedar Trail in 2026. It names neither the communities nor a parent id. Harborview accounts are not re-parented from that sentence. Marietta and Tiffin are shown as financial-hold cases only. Addresses with more than one CRM name stay investigation-only.

## Repeated runs

Finding decisions are stored with the finding id and an evidence hash. The same evidence stays decided. If the evidence changes, the finding becomes undecided and the old decision remains visible as history. Refreshing the CRM does not reapply an earlier approval. The completed Owosso duplicate is recognized from `status=Inactive` and `duplicate_of_account`, and it is not proposed again. Findlay is no longer queued once its parent is Bellhaven Senior Living.

## Daily schedule

`.github/workflows/daily-reconcile.yml` runs `python -m src.run_daily` at 12:00 UTC and on manual dispatch. The job downloads accounts with GET, scrapes the public site, and writes a report artifact. It does not set `CRM_WRITES_ENABLED`, and it exits if that variable is true. If the `BELLHAVEN_API_TOKEN` secret is missing, the scheduled job skips the refresh and finishes successfully. It has read access to the repository and cannot push commits.

Store the token as the repository secret `BELLHAVEN_API_TOKEN`. Do not put the token in the workflow file, source, or `.env` committed to git. The workflow cannot correct CRM records.

## Run locally

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app.py
```

Put the token in a local `.env` file as `BELLHAVEN_API_TOKEN`. Copy `.env.example` and fill in the value. `.env` is gitignored. The first time the review screen opens, it refreshes the public site text and downloads accounts with GET. Open the local URL Streamlit prints, normally `http://localhost:8501`.

`src.check_writeback` uses a fake session. `src.check_review` and the live portion of `src.check_phase4` read the gitignored CRM download, so run them after that GET refresh. None of these checks send a CRM write.

```powershell
.\.venv\Scripts\python.exe -m src.check_review
.\.venv\Scripts\python.exe -m src.check_writeback
.\.venv\Scripts\python.exe -m src.check_phase4
```

## Demonstration plan

1. Open the review screen. Show the disabled-writes banner, the summary counts, and the acquisition evidence expander. Point out Marietta and Tiffin as financial holds, and the multi-account addresses as investigation-only.
2. Open the Owosso duplicate. Show that the loser is already Inactive and points at `001EGU7BMJ942ZTRE6`, and that no new duplicate change is offered.
3. Show that Bellhaven Meadows of Findlay is no longer an open parent proposal. The verified account remains Active, parent `0015QAPLGS3FVYEEEM`, lifetime revenue 22000, and outstanding receivables 0.

## AI-assisted development

Cursor and an AI coding assistant were used to inspect the site and OpenAPI document, draft the matching and review code, and add tests. The assistant did not receive the API token. Endpoint paths and CRM fields came from the published docs and GET responses. Unpublished request bodies were not invented in order to send them.

## Known limitations

- POST `/api/v1/accounts` and most PATCH fields are not in the OpenAPI document. The Findlay parent body is permitted only for that one approved account. Account creation, `chow_current_account`, and name updates are not sent.
- The About page does not identify which Harborview or Cedar Trail facilities moved. Those parent changes are not proposed.
- Website presence or absence is not treated as a status change.
- `Needs Review` was not observed in the CRM, and the `note` field was not part of the verified duplicate PATCH.
- The saved website extract used for matching does not keep phone numbers. Phone comparisons in the first report came from the scrape that produced it.

## Future improvements

- If a Cedar Trail or Harborview facility is later named in an official source, run the financial rule before any parent change.
- Keep phone and administrator on the saved website extract so name discrepancies can be checked the same way on the next run.
