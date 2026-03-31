# Hosting the Documentation

The documentation site is built with [MkDocs Material](https://squidfunk.github.io/mkdocs-material/).
`mkdocs build` produces a fully static HTML site in `site/` that can be hosted anywhere
that serves static files — GitHub Pages, GitLab Pages, Nginx, S3, or Netlify.

---

## Option A: GitHub Pages (recommended)

GitHub Pages is the lowest-friction option for a repo hosted on GitHub.
A GitHub Actions workflow builds the docs on every push to `main` and deploys them automatically.

### 1. Enable GitHub Pages in the repository

1. Go to **Settings → Pages** in your GitHub repository.
2. Under **Source**, select **GitHub Actions** (not the legacy `gh-pages` branch option).
3. Save. No branch configuration is needed — the workflow below handles everything.

### 2. Add the deploy workflow

Create `.github/workflows/docs.yml`:

```yaml
name: Docs

on:
  push:
    branches: [main]
    paths:
      - 'docs/**'
      - 'mkdocs.yml'
  workflow_dispatch: # Allow manual trigger from the Actions tab

# Allow the workflow to write to GitHub Pages
permissions:
  contents: read
  pages: write
  id-token: write

# Only one deploy at a time; cancel in-progress runs on new push
concurrency:
  group: pages
  cancel-in-progress: true

jobs:
  build:
    name: Build docs
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Install MkDocs and dependencies
        run: pip install mkdocs-material

      - name: Build
        run: mkdocs build --strict

      - name: Upload Pages artifact
        uses: actions/upload-pages-artifact@v3
        with:
          path: site/

  deploy:
    name: Deploy to GitHub Pages
    needs: build
    runs-on: ubuntu-latest
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    steps:
      - name: Deploy
        id: deployment
        uses: actions/deploy-pages@v4
```

### 3. Commit and push

```bash
git add .github/workflows/docs.yml
git commit -m "Add GitHub Pages docs deploy workflow"
git push
```

The workflow runs immediately. After it completes (usually under 1 minute), the docs will
be live at `https://<org>.github.io/<repo>/` — for example:

```
https://denbi.github.io/service-registry/
```

### 4. Set the canonical URL in mkdocs.yml

Update `mkdocs.yml` to match your actual GitHub Pages URL:

```yaml
site_url: https://denbi.github.io/service-registry/
```

This ensures the sitemap and canonical link tags are correct.

---

## Option B: Self-hosted on the application server

The built static site can be served directly from the registry server. This is useful
if the repository is private and GitHub Pages would be public.

### Build and copy

```bash
# Build locally
conda run -n denbi-registry mkdocs build --strict

# Rsync to server
rsync -avz --delete site/ $DEPLOY_USER@$SERVER_HOST:/var/www/docs/
```

### Nginx vhost for the docs subdirectory

Add a `location` block inside the existing `service-registry.bi.denbi.de` server block (in
`nginx/host/service-registry.bi.denbi.de.conf`):

```nginx
location /docs/ {
    alias /var/www/docs/;
    index index.html;
    try_files $uri $uri/ $uri/index.html =404;

    # Long-lived cache for versioned assets (MkDocs adds ?h= hash params)
    location ~* \.(css|js|png|svg|woff2?)$ {
        expires 1y;
        add_header Cache-Control "public, immutable";
    }
}
```

Reload Nginx: `sudo nginx -s reload`

The docs are then at `https://service-registry.bi.denbi.de/docs/`.

Update `mkdocs.yml`:

```yaml
site_url: https://service-registry.bi.denbi.de/docs/
```

### Automate the build in CI

Add a deploy step to the existing `.github/workflows/ci.yml` that runs after tests pass
on `main`:

```yaml
deploy-docs:
  name: Deploy docs
  needs: test
  runs-on: ubuntu-latest
  if: github.ref == 'refs/heads/main' && github.event_name != 'pull_request'
  steps:
    - uses: actions/checkout@v4

    - uses: actions/setup-python@v5
      with:
        python-version: '3.12'
        cache: pip

    - name: Install MkDocs
      run: pip install mkdocs-material

    - name: Build
      run: mkdocs build --strict

    - name: Deploy to server
      uses: appleboy/scp-action@v0.1.7
      with:
        host: ${{ secrets.DEPLOY_HOST }}
        username: ${{ secrets.DEPLOY_USER }}
        key: ${{ secrets.DEPLOY_SSH_KEY }}
        source: 'site/'
        target: '/var/www/docs/'
        strip_components: 1
```

Add `DEPLOY_HOST`, `DEPLOY_USER`, and `DEPLOY_SSH_KEY` as GitHub Actions secrets.

---

## Option C: GitLab Pages

If using GitLab CI, add a `pages` job to `.gitlab-ci.yml`:

```yaml
pages:
  stage: deploy
  image: python:3.12-slim
  script:
    - pip install mkdocs-material
    - mkdocs build --strict --site-dir public
  artifacts:
    paths:
      - public
  rules:
    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH
```

GitLab automatically detects the `pages` job name and publishes `public/` to
`https://<namespace>.gitlab.io/<project>/`.

Update `mkdocs.yml`:

```yaml
site_url: https://<namespace>.gitlab.io/<project>/
```

---

## Building the docs locally

```bash
# Serve with live reload at http://127.0.0.1:8001
make docs

# Build static site into site/
make docs-build

# Build and verify no broken links or warnings (CI equivalent)
conda run -n denbi-registry mkdocs build --strict
```

---

## Keeping docs and code in sync

The `paths` filter in the GitHub Pages workflow means the workflow only runs when
`docs/**` or `mkdocs.yml` change. However, it is good practice to also regenerate
the docs when the app's public interface changes:

```yaml
on:
  push:
    branches: [main]
    paths:
      - 'docs/**'
      - 'mkdocs.yml'
      - 'apps/api/**' # API surface changes → update api-guide.md
      - 'apps/*/models.py' # Schema changes → update database-schema.md
```

This surfaces the need to update docs when models or API endpoints change, by making
the docs deploy fail to trigger (and thus the deployed site go stale) if those files
are updated without a corresponding `docs/` change.

---

## Versioned docs with mike (optional)

For projects that need to maintain docs for multiple released versions
([mike](https://github.com/jimporter/mike) is the standard tool for this with MkDocs):

```bash
pip install mike
```

Update `mkdocs.yml`:

```yaml
extra:
  version:
    provider: mike
```

Deploy a specific version:

```bash
# Tag v1.1 docs and set as latest
mike deploy --push --update-aliases v1.1 latest
mike set-default --push latest
```

In CI, replace the `mkdocs gh-deploy` step with `mike deploy`. Each version gets its
own subdirectory on the `gh-pages` branch and a version selector dropdown appears in
the navbar.

This is only needed when API or form contracts change between releases and
users need to reference docs for an older version. For a single-instance deployment
like this one, unversioned docs are simpler and sufficient.
