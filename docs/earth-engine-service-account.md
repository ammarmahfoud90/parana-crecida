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
Name it something like `parana-crecida-ci`. Skip the role prompt here; roles are
granted separately in step 2.

**2. Grant it two roles.** In *IAM & Admin → IAM*, click **Grant access**, paste
the account's email (`parana-crecida-ci@prueba-463913.iam.gserviceaccount.com`)
and add **both**:

- **Service Usage Consumer** — without it: *"Caller does not have required
  permission to use project"*
- **Earth Engine Resource Writer** — without it: *"Permission
  'earthengine.computations.create' denied"*

Each is needed, and Earth Engine only reports them one at a time, so granting
just the first looks like progress and then fails again. The legacy
`signup.earthengine.google.com` service-account page is not part of this flow for
a Cloud project.

**3. Create a key.** On the service account, *Keys → Add key → Create new key →
JSON*. A file downloads. This is a long-lived credential: anyone holding it can
act as that account.

**4. Add the secrets.** Two of them:

| Secret | Value |
|---|---|
| `EE_SERVICE_ACCOUNT_KEY` | the entire contents of the JSON file |
| `EE_PROJECT` | `prueba-463913` |

Either through GitHub's UI (*Settings → Secrets and variables → Actions → New
repository secret*), or from a terminal, which avoids the key ever touching a
clipboard — point the second command at wherever the JSON downloaded:

PowerShell (Windows):

```powershell
gh secret set EE_PROJECT --body "prueba-463913" --repo ammarmahfoud90/parana-crecida
```

```powershell
Get-Content "$env:USERPROFILE\Downloads\your-key.json" -Raw | gh secret set EE_SERVICE_ACCOUNT_KEY --repo ammarmahfoud90/parana-crecida
```

bash (macOS, Linux):

```bash
gh secret set EE_SERVICE_ACCOUNT_KEY --repo ammarmahfoud90/parana-crecida < ~/Downloads/your-key.json
```

PowerShell has no `<` input redirection, so the bash form fails there with a
parser error rather than anything helpful.

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
