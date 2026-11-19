# AppStore submission checklist

This folder is the **store bundle**: the Debian package, listing manifest, icon and 320×170 screenshots.

## 1. Fill the identity fields

`app-builder.json` ships with placeholders because the publisher's GitHub account is personal. Replace:

| Field | Value |
| --- | --- |
| `store.author.github` | your GitHub login (the account that runs `czdev login`) |
| `store.author.display_name` | the name shown on the store page |
| `store.source_repo` | `https://github.com/<you>/edgecam` |
| `store.share_code` | 4 letters/digits, unique in the store (default `EDG2`) |
| `get.sh` → `REPO` | `<you>/edgecam` |

The official validator rejects untouched template strings (`TODO`, author `M5Stack`/`Your Name`, the Template repo URL), so do not publish before replacing these.

## 2. Build / verify the package

```sh
sh build_deb.sh
dpkg-deb -I dist/edgecam_1.0.0_all.deb      # control fields
dpkg-deb -c dist/edgecam_1.0.0_all.deb      # must contain:
                                            #   usr/share/APPLaunch/bin/edgecam
                                            #   usr/share/APPLaunch/share/edgecam/edgecam.py
                                            #   usr/share/APPLaunch/applications/edgecam.desktop
                                            #   usr/share/APPLaunch/share/images/edgecam.png
```

The prebuilt `edgecam_1.0.0_all.deb` in this bundle was produced from the same tree.

Requirements checked by `czdev publish`: valid `.desktop` with `Sysplause=true`, `Exec` pointing at the launcher script, package version in the control file, no root-owned payload, size limit.

## 3. Publish

```sh
git clone https://github.com/CardputerZero/AppBuilder
cd AppBuilder
./czdev login                                  # GitHub device flow
./czdev publish --deb /path/to/edgecam_1.0.0_all.deb
```

This opens a PR against `CardputerZero/packages` with the listing metadata, screenshots, icon and a release manifest; the binary is uploaded to a Release in your fork, not to git.

- **First release of a new app** is not auto-merged: attach a short video of EDGECAM running on a real CardputerZero in the PR comments.
- Later versions merge automatically after validation. Update `VERSION`, rebuild, publish again.
- Listing-only changes (description/screenshots) use `./czdev publish --meta-only`.
- Web alternative: <https://dev.cardputer.cc> exposes the same flow.

## 4. Listing facts

- Title: `EDGECAM`
- Categories: `Media`, `Creative & Office`
- Permissions: `camera: true`; microphone / IMU / network / extra hardware / background service / external display: `false`
- Screenshots: `screenshots/01-normal.png`, `02-karax.png`, `06-zoom.png`, `04-help-zh.png` — all 320×170 PNG, rendered from a synthetic scene (no camera footage)
- Icon: `assets/icon-256.png` (256×256), also shipped as 512 px in `icon-512.png`
- License: MIT
- No network access, no telemetry, no accounts.

## 5. Unpublish

```sh
./czdev unpublish edgecam --version 1.0.0
```

Removing the last published version also removes the listing, icon and screenshots from the store.
