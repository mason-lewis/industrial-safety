# Publish only the reviewed submission copy

The working directory has no Git metadata and contains preserved historical data. Do not run `git add .` there. The prepared Git repository is `/tmp/industrial-safety-submission-20261023`; its complete copied-file list and hashes are in `SUBMISSION_MANIFEST.json`. A durable ZIP is supplied under `submission/`.

GitHub CLI authentication was invalid during implementation. The connected GitHub tool can read repositories but cannot create a repository or invite a collaborator. No repository was published, no access was changed and no course submission was sent.

After signing in with `gh auth login`, review the authenticated account and the staged copy:

```sh
cd /tmp/industrial-safety-submission-20261023
gh auth status
git status --short
git ls-files
```

Create the private repository only from this copy. Choose a different name if that repository already exists; never overwrite or force-push an existing project.

```sh
gh repo create video-analysis --private --source=. --remote=origin --push
gh repo view --json nameWithOwner,url,visibility
```

Use the returned `OWNER/REPOSITORY` in the following command to invite the instructor with read access:

```sh
gh api --method PUT repos/OWNER/REPOSITORY/collaborators/zlisto -f permission=pull
gh api repos/OWNER/REPOSITORY/collaborators/zlisto/permission
```

An invitation is not proof that the instructor has accepted or can open the repository. Verify access, clone the actual GitHub URL on a clean path, run `./start`, and record the elapsed time and prerequisites. The local clone rehearsal already passes, but it does not establish GitHub permissions.

Before final publication, collect the real-run/pilot evidence, update the report/slides, and create a new allowlist package. Keep provider credentials, operator secrets, unreviewed raw audit contents and personal pilot details out of Git. Update `submission/evidence/repository.json` with the verified URL and access status, rebuild the artifacts, and submit the URL, report.html and slides.html through the course submission channel before Monday, October 26, 6 p.m.

If the temporary Git directory is no longer available, extract the supplied ZIP into a new folder, inspect `SUBMISSION_MANIFEST.json`, initialize Git in that new folder and make its initial commit. The ZIP intentionally excludes Git internals and developer data.
