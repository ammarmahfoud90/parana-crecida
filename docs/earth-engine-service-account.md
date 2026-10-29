# Refreshing rainfall automatically

Basin rainfall is the one piece of this site that goes stale on its own. The
gauges, thresholds and ENSO all refresh in the daily GitHub Actions run; rainfall
cannot, because Earth Engine needs a Google credential and a CI runner has none.

Right now the committed series is refreshed only when someone runs:

```bash
crecida rainfall --project prueba-463913
```

To have the daily job do it instead, give it a **service account** — a Google
identity that belongs to the project rather than to you, with its own key.

The code is already in place. The workflow step is skipped while the secret is
absent, so nothing breaks either way.

## What you do

**1. Create the service account.** In the Google Cloud console for project
`prueba-463913`, go to *IAM & Admin → Service Accounts → Create service account*.
Name it something like `parana-crecida-ci`. It needs no IAM roles for this.

**2. Register it with Earth Engine.** At
[signup.earthengine.google.com/#!/service_accounts](https://signup.earthengine.google.com/#!/service_accounts),
register the account's email (it looks like
`parana-crecida-ci@prueba-463913.iam.gserviceaccount.com`). Earth Engine will not
answer it until this is done, even though the account exists.

**3. Create a key.** On the service account, *Keys → Add key → Create new key →
JSON*. A file downloads. This is a long-lived credential: anyone holding it can
act as that account.

**4. Add it as a repository secret.** In GitHub, *Settings → Secrets and variables
→ Actions → New repository secret*:

| Secret | Value |
|---|---|
| `EE_SERVICE_ACCOUNT_KEY` | the entire contents of the JSON file |
| `EE_PROJECT` | `prueba-463913` |

**5. Check it.** Run the workflow by hand
(`gh workflow run daily.yml`, or the *Run workflow* button) and look at the
*Refresh basin rainfall* step. It should run rather than skip, and
`data/rainfall.json` should come back in the run's commit with a fresh
`fetched_on`.

## Worth knowing before you do it

- **The key does not expire.** Unlike your own `earthengine authenticate` token,
  it keeps working until you delete it. If the repository is public, the secret
  itself is not exposed — GitHub does not give secrets to workflows triggered by
  pull requests from forks — but it is still a standing credential on your Google
  account, and that is the real cost of this convenience.
- **Scope it narrowly.** The account needs no IAM roles in the project. Earth
  Engine registration is what grants it access, and that is all it should have.
- **You can revoke it** at any time from the same Keys page, and the workflow goes
  back to skipping the step rather than failing.
- **If you skip this entirely**, nothing breaks. `crecida freshness` fails the
  daily run once the rainfall is more than 10 days old, so you will be told rather
  than quietly serving old rain. Running `crecida rainfall` locally every week or
  two is a perfectly reasonable alternative.

## How the code picks a credential

`rainfall.initialize()` looks for `EE_SERVICE_ACCOUNT_KEY` first, accepting either
the JSON itself or a path to it, and falls back to the local
`earthengine authenticate` token when it is absent. The project id comes from
`--project`, then `EE_PROJECT`, then the key's own `project_id`.
