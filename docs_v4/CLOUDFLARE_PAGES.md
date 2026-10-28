# Private V4 dashboard on Cloudflare Pages (owner-only)

## How it works
1. **New odds trigger a build.** `odds-collect` saves a new odds shard, or `cfb-rankings` saves a new AP poll. The `site-build` workflow then rebuilds the dashboard from collected data. It makes no Odds API calls.
2. **Release copy.** The built page is stored as a private GitHub release asset (`v4-site`), as before.
3. **Cloudflare deploy.** If repository variable `CF_PAGES_DEPLOY` is `on`, two more steps run:
   - **Check (no secrets).** Requests `https://<project>.pages.dev/` and a random `https://<hash>.<project>.pages.dev/` without logging in. Both must redirect to a `*.cloudflareaccess.com` login, or the deploy is **refused**: private data is never uploaded to an unprotected address.
   - **Deploy (Cloudflare token only).** Reads the project's production branch from the Cloudflare API and uploads `index.html`, security headers (`deploy/cloudflare/_headers`: noindex, no-store, strict CSP, no framing) and a disallow-all `robots.txt` with wrangler. Wrangler is pinned by `deploy/cloudflare/package-lock.json` and installed with `npm ci --ignore-scripts`. The step then confirms Cloudflare reports the deployment as successful.
4. **Freshness.** The page labels itself LIVE, DELAYED or STALE in your browser from the time the odds were observed.

The Odds API and CollegeFootballData keys never enter the build or deploy steps. Callers pass only the two Cloudflare secrets, never `secrets: inherit`.

## Cost
- **Cloudflare:** Pages free plan, and Access free for up to 50 users. Deploys happen only after new data, roughly 80–120 a month at the current budget, below the free plan's 500 builds a month even if uploads counted.
- **GitHub Actions:** about 1 extra minute per build.

## Owner setup (one time)
1. **Cloudflare Access must cover both hostnames.**
   - Pages' "Enable access policy" switch protects only the preview hostnames (`*.arbitredge-v4-staging.pages.dev`), not the main address.
   - Follow Cloudflare's steps in Pages *Known issues*, "Enable Access on your `*.pages.dev` domain". You should end with two Access applications, each with the policy *ArbitrEdge Owner Only*:
     - `arbitredge-v4-staging.pages.dev`
     - `*.arbitredge-v4-staging.pages.dev`
   - If you add a custom domain later, it needs its own Access application.
2. **API token.** In Cloudflare: My Profile → API Tokens → Create Token → Custom token.
   - Permissions: **Account → Cloudflare Pages → Edit**.
   - Account resources: only your account.
   - No other permissions.
3. **Account ID.** Workers & Pages overview page (right-hand side).
4. **GitHub secrets.** Repository → Settings → Secrets and variables → Actions → *Secrets*:
   - `CLOUDFLARE_API_TOKEN`
   - `CLOUDFLARE_ACCOUNT_ID`
5. **GitHub variables.** Same page → *Variables*:
   - `CF_PAGES_PROJECT` = `arbitredge-v4-staging`
   - `CF_PAGES_DEPLOY` = `on`

## Why the site showed Error 522
The project had **no deployment**. Nothing in the repository deployed to Cloudflare, so after the Access login there was no origin to serve. The first successful deploy fixes it.

## Turning it off
Set `CF_PAGES_DEPLOY` to anything other than `on`. Builds then continue to the GitHub release only. Revoking the API token in Cloudflare also stops deploys immediately, and the deploy step then fails loudly.
